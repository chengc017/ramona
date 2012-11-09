import logging
import pywintypes # from Python Win32
from ..winsvc import w32_install_svc

###

name = 'wininstall'
cmdhelp = 'Install Ramona as Windows Service (Windows only)'

###


L = logging.getLogger('wininstall')

###

def init_parser(parser):
	parser.add_argument('-d', '--dont-start', action='store_true', help="Don't start service after installation.")
	
	parser.add_argument('-S','--server-only', action='store_true', help='Start only server, programs are not launched when service is launched')
	parser.add_argument('program', nargs='*', help='Optionally specify program(s) to start when service is launched (if nothing is specified, all enabled programs will be launched)')

	#TODO: Auto-start of service (after reboot) enable (default) / disable

###

def main(cnsapp, args):
	try:
		cls = w32_install_svc(
			start=not args.dont_start,
			server_only=args.server_only,
			programs=args.program if len(args.program) > 0 else None
		)
	except pywintypes.error, e:
		L.error("Error when installing Windows service: {0} ({1})".format(e.strerror, e.winerror))
		return 3 # Exit code

	if args.dont_start:
		L.info("Windows service '{0}' has been installed successfully.".format(cls._svc_name_))
	else:
		L.info("Windows service '{0}' has been installed and started successfully.".format(cls._svc_name_))
