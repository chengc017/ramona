import sys, socket, errno, logging, mimetypes, json, signal
from ..config import config, read_config
from .. import cnscom
import BaseHTTPServer
import httplib
import os
import ConfigParser
import time
from ..cnscom import program_state_enum
import cgi
import pprint
import urllib
import urlparse
import itertools

###

L = logging.getLogger("httpfendapp")

###

# Initialize mimetypes
if not mimetypes.inited:
		mimetypes.init()

class httpfend_app(object):

	instance = None

	def __init__(self):
		assert self.instance is None
		httpfend_app.instance = self
		
		# Read config
		read_config()

		# Configure logging
		logging.basicConfig(
			level=logging.DEBUG,
			stream=sys.stderr,
			format="%(levelname)s: %(message)s"
		)

		try:
			host = config.get(os.environ['RAMONA_SECTION'], 'host')
			port = config.getint(os.environ['RAMONA_SECTION'], 'port')
		except ConfigParser.NoSectionError, e:
			L.fatal("Missing configuration section {0}. Exiting.".format(os.environ['RAMONA_SECTION']))
			sys.exit(1)
		except ConfigParser.NoOptionError, e:
			L.fatal("Missing configuration option: {0}. Exiting".format(e))
			sys.exit(1)

		Handler = RamonaHttpReqHandler
		self.httpd = BaseHTTPServer.HTTPServer((host, port), Handler)
		
		# Prepare server connection factory
		self.cnsconuri = cnscom.socket_uri(config.get('ramona:console','serveruri'))
		
		self.logmsgcnt = itertools.count()
		self.logmsgs = dict()
		signal.signal(signal.SIGINT, self.stop)

	def run(self):
		L.info("Started HTTP frontend at http://{0}:{1}".format(self.httpd.server_name, self.httpd.server_port))
		self.httpd.serve_forever()
	
	def stop(self, signum, frame):
		L.info("Received signal {0}. Stopping the server.".format(signum))
		self.httpd.shutdown()


class RamonaHttpReqHandler(BaseHTTPServer.BaseHTTPRequestHandler):
	
	ActionToCallid = {"start": cnscom.callid_start, "stop": cnscom.callid_stop, "restart": cnscom.callid_restart}
	
	def do_GET(self):
		scriptdir = os.path.join(".", "ramona", "httpfend")
		if self.path.startswith("/static/"):
			parts = self.path.split("/")
			fname = os.path.join(scriptdir, *[x for x in parts if len(x) > 0])
			if not os.path.isfile(fname):
				self.send_error(httplib.NOT_FOUND)
				return
			try:
				f = open(fname, "r")
			except IOError:
				self.send_error(httplib.NOT_FOUND)
				return
			with f:
				self.send_response(httplib.OK)
				self.send_header("Content-Type", mimetypes.guess_type(self.path)[0])
				self.end_headers()
				self.wfile.write(f.read())
		elif self.path.startswith("/ajax/"):
			parsed = urlparse.urlparse(self.path)
			action = parsed.path[6:]
			if action == "statusTable":
				self.send_response(httplib.OK)
				self.send_header("Content-Type", "text/html; charset=utf-8")
				self.end_headers()
				self.wfile.write(self.buildStatusTable(json.loads(self.getStatuses())))
		elif self.path.startswith("/log/"):
			parsed = urlparse.urlparse(self.path)
			logname = parsed.path[5:]
			logdir = config.get("general", "logdir")
			logpath = os.path.abspath(os.path.join(".", logdir, logname))
			try:
				with open(logpath, "r") as f:
					self.send_response(httplib.OK)
					self.send_header("Content-Type", "text/plain; charset=utf-8")
					self.end_headers()
					self.wfile.write(f.read())
			except Exception, e:
				self.send_error(httplib.NOT_FOUND, str(e))
				return
				
			
		else:
			parsed = urlparse.urlparse(self.path)
			if parsed.path != "/":
				self.send_error(httplib.NOT_FOUND)
				return
			
			qs = urlparse.parse_qs(parsed.query)
			action = None
			actionList = qs.get('action')
			if actionList is not None and len(actionList) > 0:
				action = actionList[0]
			if action in ("start", "stop", "restart"):
				conn = self.socket_connect()
				params = {}
				qsIdent = qs.get('ident')
				if qsIdent is not None and len(qsIdent) > 0:
					params['pfilter'] = [qsIdent[0]]
				else:
					params['pfilter'] = list(self.getAllPrograms(True))
				
				qsForce = qs.get('force')
				if qsForce is not None and len(qsForce) > 0:
					if qsForce[0] == "1":
						params['force'] = True
				
				try:
					cnscom.svrcall(conn, self.ActionToCallid[action], json.dumps(params))
					msgid = self.addLogMessage("success", "Command successfully triggered.")
				except Exception, e:
					msgid = self.addLogMessage("error", "Failed to trigger the command: {0}".format(e))
				
				self.send_response(httplib.SEE_OTHER)
				self.send_header("Location", self.getAbsPath(msgid=msgid))
				self.end_headers()
				return
								
			
			self.send_response(httplib.OK)
			self.send_header("Content-Type", "text/html; charset=utf-8")
			self.end_headers()
			
			logmsg = ""
			
			for msgid in qs.get('msgid', []):
				m = httpfend_app.instance.logmsgs.pop(int(msgid), None)
				if m is not None:
					level, msg = m
					
					logmsg += '''<div class="alert alert-{0}">
					  <button type="button" class="close" data-dismiss="alert">&times;</button>
					  {1}
					</div>'''.format(level, msg)
			with open(os.path.join(scriptdir, "index.tmpl.html")) as f:
				sttable = self.buildStatusTable(json.loads(self.getStatuses()))
				self.wfile.write(f.read().format(statuses=sttable, logmsg=logmsg))
			
	
	def buildStatusTable(self, statuses):
		ret = '<table id="statusTable" class="table table-hover table-bordered"><thead>'
		ret += '<tr><th>Name</th><th>Status</th><th>PID</th><th>Launches</th><th>Start time</th><th>Terminate time</th><th>Log</th><th></th></tr>'
		ret += "</thead>"
		ret += "<tbody>"
		for sp in statuses:

			ret += "<tr>"
			ident = sp.pop('ident', '???')
			ret += '<th>{0}</th>'.format(cgi.escape(ident))
			labelCls = "label-inverse"
			progState = sp.pop("state")
			
			if progState == program_state_enum.RUNNING:
				labelCls = "label-success"
			elif progState == program_state_enum.STOPPED:
				labelCls = ""
			elif progState in (program_state_enum.STOPPING, program_state_enum.STARTING):
				labelCls = "label-info"
			elif progState == program_state_enum.STOPPED:
				labelCls = ""
			elif progState in (program_state_enum.FATAL, program_state_enum.CFGERROR):
				labelCls = "label-important"
			
			
			ret += '<td><span class="label {0}">{1}</span></td>'.format(labelCls, cgi.escape(sp.pop('stlbl', '???')))
			pid = sp.pop('pid', "")
			ret += '<td>{0}</td>'.format(pid)
			ret += '<td>{0}</td>'.format(sp.pop('launch_cnt', ""))
			t = sp.pop('start_time')
			tform = ""
			if t is not None: tform = time.strftime("%d-%m-%Y %H:%M:%S",time.localtime(t))
			ret += '<td>{0}</td>'.format(tform)
			t = sp.pop('term_time')
			tform = ""
			if t is not None: tform = time.strftime("%d-%m-%Y %H:%M:%S",time.localtime(t))
			ret += '<td>{0}</td>'.format(tform)
			
			ret += '<td><a href="/log/{0}.log">{0}.log</a></td>'.format(cgi.escape(ident))
			
			actions = []
			if pid != os.getpid():
				# TODO: Should there be some filtering for STOPPING ???
				if progState not in (program_state_enum.FATAL, program_state_enum.RUNNING, program_state_enum.STARTING):
					actions.append('<a class="btn btn-small btn-success" href="/?{0}">Start</a>'.format(cgi.escape(urllib.urlencode([("action", "start"), ("ident", ident)]))))
				
				if progState == program_state_enum.RUNNING:
					actions.append('<a class="btn btn-small btn-danger" href="/?{0}">Stop</a>'.format(cgi.escape(urllib.urlencode([("action", "stop"), ("ident", ident)]))))
					actions.append('<a class="btn btn-small btn-warning" href="/?{0}">Restart</a>'.format(cgi.escape(urllib.urlencode([("action", "restart"), ("ident", ident)]))))
			
				if progState == program_state_enum.FATAL:
					actions.append('<a class="btn btn-small btn-inverse" href="/?{0}">Start (force)</a>'.format(cgi.escape(urllib.urlencode([("action", "start"), ("ident", ident), ("force", "1")]))))
				
			ret += '<td>{0}</td>'.format(" ".join(actions))
			
			ret += "</tr>"
			
			if len(sp) > 0:
				ret += '<tr class="info"><td colspan="2"></td><td colspan="5"><pre class="pre-scrollable">'
				ret += cgi.escape(pprint.pformat(sp, width=3))
				ret += '</pre></td></tr>'
		
		ret += "</tbody></table>"
		return ret
	
	
	def addLogMessage(self, level, msg):
		msgid = httpfend_app.instance.logmsgcnt.next()
		httpfend_app.instance.logmsgs[msgid] = (level, msg)
		return msgid
	
	def getAbsPath(self, path="/", **kwargs):
		queryList = []
		for k,v in kwargs.iteritems():
			queryList.append((k, v))
		
		return urlparse.urlunparse(("http", self.headers['Host'], "/", None, urllib.urlencode(queryList), None))
	
	def getStatuses(self):
		conn = self.socket_connect()
		return cnscom.svrcall(conn, cnscom.callid_status, json.dumps({}))
	
	def getAllPrograms(self, withoutSelf=False):
		'''
		@param withoutSelf: If true, the process with the same pid will be ignored
		@return iterator: Ident of all registered programs
		'''
		for st in json.loads(self.getStatuses()):
			ident = st.get("ident")
			pid = st.get("pid")
			if ident is None:
				continue
			if (not withoutSelf) or (pid != os.getpid()):
				yield ident
		
	
	def socket_connect(self):
		if hasattr(self, "socket_conn"): return self.socket_conn
		try:
			self.socket_conn = httpfend_app.instance.cnsconuri.create_socket_connect()
			return self.socket_conn
		except socket.error, e:
			if e.errno == errno.ECONNREFUSED: return None
			if e.errno == errno.ENOENT and self.cnsconuri.protocol == 'unix': return None
			raise

