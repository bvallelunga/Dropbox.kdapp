#!/usr/bin/python
#
# Copyright (c) Dropbox, Inc.
#
# dropbox
# Dropbox frontend script
# This file is part of nautilus-dropbox 1.6.1.
#
# nautilus-dropbox is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# nautilus-dropbox is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with nautilus-dropbox.  If not, see <http://www.gnu.org/licenses/>.
#
from __future__ import with_statement

import errno
import locale
import optparse
import os
import platform
import shutil
import socket
import StringIO
import subprocess
import sys
import tarfile
import tempfile
import threading
import thread
import time
import traceback
import urllib2

try:
    import gpgme
except ImportError:
    gpgme = None

from contextlib import closing, contextmanager
from posixpath import curdir, sep, pardir, join, abspath, commonprefix

INFO = u"Dropbox is the easiest way to share and store your files online. Want to learn more? Head to"
LINK = u"https://www.dropbox.com/"
WARNING = u"In order to use Dropbox, you must download the proprietary daemon."
GPG_WARNING = u"Note: python-gpgme is not installed, we will not be able to verify binary signatures."
ERROR_CONNECTING = u"Trouble connecting to Dropbox servers. Maybe your internet connection is down, or you need to set your http_proxy environment variable."
ERROR_SIGNATURE = u"Downloaded binary does not match Dropbox signature, aborting install."

DOWNLOAD_LOCATION_FMT = "https://www.dropbox.com/download?plat=%s"
SIGNATURE_LOCATION_FMT = "https://www.dropbox.com/download?plat=%s&signature=1"

DOWNLOADING = u"Downloading Dropbox... %d%%"
UNPACKING = u"Unpacking Dropbox... %d%%"

PARENT_DIR = os.path.expanduser("~")
DROPBOXD_PATH = "%s/.dropbox-dist/dropboxd" % PARENT_DIR
DESKTOP_FILE = u"/usr/share/applications/dropbox.desktop"

enc = locale.getpreferredencoding()

# Available from https://linux.dropbox.com/fedora/rpm-public-key.asc
DROPBOX_PUBLIC_KEY = """
-----BEGIN PGP PUBLIC KEY BLOCK-----
Version: SKS 1.1.0

mQENBEt0ibEBCACv4hZRPqwtpU6z8+BB5YZU1a3yjEvg2W68+a6hEwxtCa2U++4dzQ+7EqaU
q5ybQnwtbDdpFpsOi9x31J+PCpufPUfIG694/0rlEpmzl2GWzY8NqfdBFGGm/SPSSwvKbeNc
FMRLu5neo7W9kwvfMbGjHmvUbzBUVpCVKD0OEEf1q/Ii0Qcekx9CMoLvWq7ZwNHEbNnij7ec
nvwNlE2MxNsOSJj+hwZGK+tM19kuYGSKw4b5mR8IyThlgiSLIfpSBh1n2KX+TDdk9GR+57TY
vlRu6nTPu98P05IlrrCP+KF0hYZYOaMvQs9Rmc09tc/eoQlN0kkaBWw9Rv/dvLVc0aUXABEB
AAG0MURyb3Bib3ggQXV0b21hdGljIFNpZ25pbmcgS2V5IDxsaW51eEBkcm9wYm94LmNvbT6J
ATYEEwECACAFAkt0ibECGwMGCwkIBwMCBBUCCAMEFgIDAQIeAQIXgAAKCRD8kYszUESRLi/z
B/wMscEa15rS+0mIpsORknD7kawKwyda+LHdtZc0hD/73QGFINR2P23UTol/R4nyAFEuYNsF
0C4IAD6y4pL49eZ72IktPrr4H27Q9eXhNZfJhD7BvQMBx75L0F5gSQwuC7GdYNlwSlCD0AAh
Qbi70VBwzeIgITBkMQcJIhLvllYo/AKD7Gv9huy4RLaIoSeofp+2Q0zUHNPl/7zymOqu+5Ox
e1ltuJT/kd/8hU+N5WNxJTSaOK0sF1/wWFM6rWd6XQUP03VyNosAevX5tBo++iD1WY2/lFVU
JkvAvge2WFk3c6tAwZT/tKxspFy4M/tNbDKeyvr685XKJw9ei6GcOGHD
=5rWG
-----END PGP PUBLIC KEY BLOCK-----
"""

# Futures

def methodcaller(name, *args, **kwargs):
    def caller(obj):
        return getattr(obj, name)(*args, **kwargs)
    return caller

def relpath(path, start=curdir):
    """Return a relative version of a path"""

    if not path:
        raise ValueError("no path specified")

    if type(start) is unicode:
        start_list = unicode_abspath(start).split(sep)
    else:
        start_list = abspath(start).split(sep)

    if type(path) is unicode:
        path_list = unicode_abspath(path).split(sep)
    else:
        path_list = abspath(path).split(sep)

    # Work out how much of the filepath is shared by start and path.
    i = len(commonprefix([start_list, path_list]))

    rel_list = [pardir] * (len(start_list)-i) + path_list[i:]
    if not rel_list:
        return curdir
    return join(*rel_list)

# End Futures


def console_print(st=u"", f=sys.stdout, linebreak=True):
    global enc
    assert type(st) is unicode
    f.write(st.encode(enc))
    if linebreak: f.write(os.linesep)

def console_flush(f=sys.stdout):
    f.flush()

def yes_no_question(question):
    while True:
        console_print(question, linebreak=False)
        console_print(u" [y/n] ", linebreak=False)
        console_flush()
        text = raw_input()
        if text.lower().startswith("y"):
            return True
        elif text.lower().startswith("n"):
            return False
        else:
            console_print(u"Sorry, I didn't understand that. Please type yes or no.")

def plat():
    if sys.platform.lower().startswith('linux'):
        arch = platform.machine()
        if (arch[0] == 'i' and
            arch[1].isdigit() and
            arch[2:4] == '86'):
            plat = "x86"
        elif arch == 'x86_64':
            plat = arch
        else:
            FatalVisibleError("Platform not supported")
        return "lnx.%s" % plat
    else:
        FatalVisibleError("Platform not supported")

def is_dropbox_running():
    pidfile = os.path.expanduser("~/.dropbox/dropbox.pid")

    try:
        with open(pidfile, "r") as f:
            pid = int(f.read())
        with open("/proc/%d/cmdline" % pid, "r") as f:
            cmdline = f.read().lower()
    except:
        cmdline = ""

    return "dropbox" in cmdline

def unicode_abspath(path):
    global enc
    assert type(path) is unicode
    # shouldn't pass unicode to this craphead, it appends with os.getcwd() which is always a str
    return os.path.abspath(path.encode(sys.getfilesystemencoding())).decode(sys.getfilesystemencoding())

@contextmanager
def gpgme_context(keys):
    gpg_conf_contents = ''
    _gpghome = tempfile.mkdtemp(prefix='tmp.gpghome')

    try:
        os.environ['GNUPGHOME'] = _gpghome
        fp = open(os.path.join(_gpghome, 'gpg.conf'), 'wb')
        fp.write(gpg_conf_contents)
        fp.close()
        ctx = gpgme.Context()

        loaded = []
        for key_file in keys:
            result = ctx.import_(key_file)
            key = ctx.get_key(result.imports[0][0])
            loaded.append(key)

        ctx.signers = loaded

        yield ctx
    finally:
        del os.environ['GNUPGHOME']
        shutil.rmtree(_gpghome, ignore_errors=True)

class SignatureVerifyError(Exception):
    pass

def verify_signature(key_file, sig_file, plain_file):
    with gpgme_context([key_file]) as ctx:
        sigs = ctx.verify(sig_file, plain_file, None)
        return sigs[0].status == None

def download_file_chunk(url, buf):
    opener = urllib2.build_opener()
    opener.addheaders = [('User-Agent', "DropboxLinuxDownloader/1.6.1")]
    sock = opener.open(url)

    size = int(sock.info()['content-length'])
    bufsize = max(size / 200, 4096)
    progress = 0

    with closing(sock) as f:
        yield (0, True)
        while True:
            try:
                chunk = f.read(bufsize)
                progress += len(chunk)
                buf.write(chunk)
                yield (float(progress)/size, True)
                if progress == size:
                    break
            except OSError, e:
                if hasattr(e, 'errno') and e.errno == errno.EAGAIN:
                    # nothing left to read
                    yield (float(progress)/size, False)
                else:
                    raise

class DownloadState(object):
    def __init__(self):
        self.local_file = StringIO.StringIO()

    def copy_data(self):
        return download_file_chunk(DOWNLOAD_LOCATION_FMT % plat(), self.local_file)

    def unpack(self):
        # download signature
        signature = StringIO.StringIO()
        for _ in download_file_chunk(SIGNATURE_LOCATION_FMT % plat(), signature):
            pass
        signature.seek(0)
        self.local_file.seek(0)

        if gpgme:
            if not verify_signature(StringIO.StringIO(DROPBOX_PUBLIC_KEY), signature, self.local_file):
                raise SignatureVerifyError()

        self.local_file.seek(0)
        archive = tarfile.open(fileobj=self.local_file, mode='r:gz')
        total_members = len(archive.getmembers())
        for i, member in enumerate(archive.getmembers()):
            archive.extract(member, PARENT_DIR)
            yield member.name, i, total_members
        archive.close()

    def cancel(self):
        if not self.local_file.closed:
            self.local_file.close()

def download(writeLog = False):
    global FatalVisibleError
    def FatalVisibleError(s):
        console_print(u"\nError: %s" % s, f=sys.stderr)
        sys.exit(-1)


    ESC = "\x1b"
    save = ESC+"7"
    unsave = ESC+"8"
    clear = ESC+"[2J"
    erase_to_start = ESC+"[1K"
    write = sys.stdout.write
    flush = sys.stdout.flush

    DOWNLOAD_OUT = "/tmp/_dropbox.download"
    last_progress = [None, None]
    def setprogress(text, frac):
        if last_progress == [text, frac]:
            return
        if sys.stdout.isatty():
            write(erase_to_start)
            write(unsave)
        console_print(text % int(100*frac), linebreak=not sys.stdout.isatty())
 	if writeLog:
            open(DOWNLOAD_OUT, "w").write(text % int(100*frac))
        if sys.stdout.isatty():
            flush()
        last_progress[0], last_progress[1] = text, frac

    console_print()
    if sys.stdout.isatty():
        write(save)
        flush()
    console_print(u"%s %s\n" % (INFO, LINK))
    GPG_WARNING_MSG = (u"\n%s" % GPG_WARNING) if not gpgme else u""

    # if not yes_no_question("%s%s" % (WARNING, GPG_WARNING_MSG)):
    #     return

    download = DownloadState()

    try:
        for progress, status in download.copy_data():
            if not status:
                break
            setprogress(DOWNLOADING, progress)
    except Exception:
        FatalVisibleError(ERROR_CONNECTING)
    else:
        setprogress(DOWNLOADING, 1.0)
        console_print()
        write(save)
    finally:
        if os.path.exists(DOWNLOAD_OUT):
            os.remove(DOWNLOAD_OUT)

    try:
        for name, i, total in download.unpack():
            setprogress(UNPACKING, float(i)/total)
    except SignatureVerifyError:
        FatalVisibleError(ERROR_SIGNATURE)
    except Exception:
        FatalVisibleError(ERROR_CONNECTING)
    else:
        setprogress(UNPACKING, 1.0)
    finally:
        if os.path.exists(DOWNLOAD_OUT):
            os.remove(DOWNLOAD_OUT)

    console_print()

class CommandTicker(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        ticks = ['[.  ]', '[.. ]', '[...]', '[ ..]', '[  .]', '[   ]']
        i = 0
        first = True
        while True:
            self.stop_event.wait(0.25)
            if self.stop_event.isSet(): break
            if i == len(ticks):
                first = False
                i = 0
            if not first:
                sys.stderr.write("\r%s\r" % ticks[i])
                sys.stderr.flush()
            i += 1
        sys.stderr.flush()


class DropboxCommand(object):
    class CouldntConnectError(Exception): pass
    class BadConnectionError(Exception): pass
    class EOFError(Exception): pass
    class CommandError(Exception): pass

    def __init__(self, timeout=5):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(timeout)
        try:
            self.s.connect(os.path.expanduser(u'~/.dropbox/command_socket'))
        except socket.error, e:
            raise DropboxCommand.CouldntConnectError()
        self.f = self.s.makefile("r+", 4096)

    def close(self):
        self.f.close()
        self.s.close()

    def __readline(self):
        try:
            toret = self.f.readline().decode('utf8').rstrip(u"\n")
        except socket.error, e:
            raise DropboxCommand.BadConnectionError()
        if toret == '':
            raise DropboxCommand.EOFError()
        else:
            return toret

    # atttribute doesn't exist, i know what you want
    def send_command(self, name, args):
        self.f.write(name.encode('utf8'))
        self.f.write(u"\n".encode('utf8'))
        self.f.writelines((u"\t".join([k] + (list(v)
                                             if hasattr(v, '__iter__') else
                                             [v])) + u"\n").encode('utf8')
                          for k,v in args.iteritems())
        self.f.write(u"done\n".encode('utf8'))

        self.f.flush()

        # Start a ticker
        ticker_thread = CommandTicker()
        ticker_thread.start()

        # This is the potentially long-running call.
        try:
            ok = self.__readline() == u"ok"
        except KeyboardInterrupt:
            raise DropboxCommand.BadConnectionError("Keyboard interruption detected")
        finally:
            # Tell the ticker to stop.
            ticker_thread.stop()
            ticker_thread.join()

        if ok:
            toret = {}
            for i in range(21):
                if i == 20:
                    raise Exception(u"close this connection!")

                line = self.__readline()
                if line == u"done":
                    break

                argval = line.split(u"\t")
                toret[argval[0]] = argval[1:]

            return toret
        else:
            problems = []
            for i in range(21):
                if i == 20:
                    raise Exception(u"close this connection!")

                line = self.__readline()
                if line == u"done":
                    break

                problems.append(line)

            raise DropboxCommand.CommandError(u"\n".join(problems))

    # this is the hotness, auto marshalling
    def __getattr__(self, name):
        try:
            return super(DropboxCommand, self).__getattr__(name)
        except:
            def __spec_command(**kw):
                return self.send_command(unicode(name), kw)
            self.__setattr__(name, __spec_command)
            return __spec_command

commands = {}
aliases = {}

def command(meth):
    global commands, aliases
    assert meth.__doc__, "All commands need properly formatted docstrings (even %r!!)" % meth
    if hasattr(meth, 'im_func'): # bound method, if we ever have one
        meth = meth.im_func
    commands[meth.func_name] = meth
    meth_aliases = [unicode(alias) for alias in aliases.iterkeys() if aliases[alias].func_name == meth.func_name]
    if meth_aliases:
        meth.__doc__ += u"\nAliases: %s" % ",".join(meth_aliases)
    return meth

def alias(name):
    def decorator(meth):
        global commands, aliases
        assert name not in commands, "This alias is the name of a command."
        aliases[name] = meth
        return meth
    return decorator

def requires_dropbox_running(meth):

    def newmeth(*n, **kw):

    	if installed() != 1:
            console_print(u"Dropbox is not installed!")
	    return 4
        elif is_dropbox_running():
            return meth(*n, **kw)
        else:
            console_print(u"Dropbox isn't running!")

    newmeth.func_name = meth.func_name
    newmeth.__doc__ = meth.__doc__
    return newmeth

def start_dropbox():
    db_path = os.path.expanduser(u"~/.dropbox-dist/dropboxd").encode(sys.getfilesystemencoding())
    if os.access(db_path, os.X_OK):
        f = open("/tmp/_dropbox.out", "w")
        # we don't reap the child because we're gonna die anyway, let init do it
        a = subprocess.Popen([db_path], preexec_fn=os.setsid, cwd=os.path.expanduser("~"),
                             stderr=sys.stderr, stdout=f, close_fds=True)

        # in seconds
        interval = 0.5
        wait_for = 60
        for i in xrange(int(wait_for / interval)):
            if is_dropbox_running():
            	return True
            # back off from connect for a while
            time.sleep(interval)

        return False
    else:
        return False

# Extracted and modified from os.cmd.Cmd
def columnize(list, display_list=None, display_width=None):
    if not list:
        console_print(u"<empty>")
        return

    non_unicode = [i for i in range(len(list)) if not (isinstance(list[i], unicode))]
    if non_unicode:
        raise TypeError, ("list[i] not a string for i in %s" %
                          ", ".join(map(unicode, non_unicode)))

    if not display_width:
        d = os.popen('stty size', 'r').read().split()
        if d:
            display_width = int(d[1])
        else:
            for item in list:
                console_print(item)
            return

    if not display_list:
        display_list = list

    size = len(list)
    if size == 1:
        console_print(display_list[0])
        return

    for nrows in range(1, len(list)):
        ncols = (size+nrows-1) // nrows
        colwidths = []
        totwidth = -2
        for col in range(ncols):
            colwidth = 0
            for row in range(nrows):
                i = row + nrows*col
                if i >= size:
                    break
                x = list[i]
                colwidth = max(colwidth, len(x))
            colwidths.append(colwidth)
            totwidth += colwidth + 2
            if totwidth > display_width:
                break
        if totwidth <= display_width:
            break
    else:
        nrows = len(list)
        ncols = 1
        colwidths = [0]
    lines = []
    for row in range(nrows):
        texts = []
        display_texts = []
        for col in range(ncols):
            i = row + nrows*col
            if i >= size:
                x = ""
                y = ""
            else:
                x = list[i]
                y = display_list[i]
            texts.append(x)
            display_texts.append(y)
        while texts and not texts[-1]:
            del texts[-1]
        original_texts = texts[:]
        for col in range(len(texts)):
            texts[col] = texts[col].ljust(colwidths[col])
            texts[col] = texts[col].replace(original_texts[col], display_texts[col])
        line = u"  ".join(texts)
        lines.append(line)
    for line in lines:
        console_print(line)

@command
@requires_dropbox_running
@alias('stat')
def filestatus(args):
    u"""get current sync status of one or more files
dropbox filestatus [-l] [-a] [FILE]...

Prints the current status of each FILE.

options:
  -l --list  prints out information in a format similar to ls. works best when your console supports color :)
  -a --all   do not ignore entries starting with .
"""
    global enc

    oparser = optparse.OptionParser()
    oparser.add_option("-l", "--list", action="store_true", dest="list")
    oparser.add_option("-a", "--all", action="store_true", dest="all")
    (options, args) = oparser.parse_args(args)

    try:
        with closing(DropboxCommand()) as dc:
            if options.list:
                # Listing.

                # Separate directories from files.
                if len(args) == 0:
                    dirs, nondirs = [u"."], []
                else:
                    dirs, nondirs = [], []

                    for a in args:
                        try:
                            (dirs if os.path.isdir(a) else nondirs).append(a.decode(enc))
                        except UnicodeDecodeError:
                            continue

                    if len(dirs) == 0 and len(nondirs) == 0:
                        #TODO: why?
                        exit(1)

                dirs.sort(key=methodcaller('lower'))
                nondirs.sort(key=methodcaller('lower'))

                # Gets a string representation for a path.
                def path_to_string(file_path):
                    if not os.path.exists(file_path):
                        path = u"%s (File doesn't exist!)" % os.path.basename(file_path)
                        return (path, path)
                    try:
                        status = dc.icon_overlay_file_status(path=file_path).get(u'status', [None])[0]
                    except DropboxCommand.CommandError, e:
                        path =  u"%s (%s)" % (os.path.basename(file_path), e)
                        return (path, path)

                    env_term = os.environ.get('TERM','')
                    supports_color = (sys.stderr.isatty() and (
                                        env_term.startswith('vt') or
                                        env_term.startswith('linux') or
                                        'xterm' in env_term or
                                        'color' in env_term
                                        )
                                     )

                    # TODO: Test when you don't support color.
                    if not supports_color:
                        path = os.path.basename(file_path)
                        return (path, path)

                    if status == u"up to date":
                        init, cleanup = "\x1b[32;1m", "\x1b[0m"
                    elif status == u"syncing":
                        init, cleanup = "\x1b[36;1m", "\x1b[0m"
                    elif status == u"unsyncable":
                        init, cleanup = "\x1b[41;1m", "\x1b[0m"
                    elif status == u"selsync":
                        init, cleanup = "\x1b[37;1m", "\x1b[0m"
                    else:
                        init, cleanup = '', ''

                    path = os.path.basename(file_path)
                    return (path, u"%s%s%s" % (init, path, cleanup))

                # Prints a directory.
                def print_directory(name):
                    clean_paths = []
                    formatted_paths = []
                    for subname in sorted(os.listdir(name), key=methodcaller('lower')):
                        if type(subname) != unicode:
                            continue

                        if not options.all and subname[0] == u'.':
                            continue

                        try:
                            clean, formatted = path_to_string(unicode_abspath(os.path.join(name, subname)))
                            clean_paths.append(clean)
                            formatted_paths.append(formatted)
                        except (UnicodeEncodeError, UnicodeDecodeError), e:
                            continue

                    columnize(clean_paths, formatted_paths)

                try:
                    if len(dirs) == 1 and len(nondirs) == 0:
                        print_directory(dirs[0])
                    else:
                        nondir_formatted_paths = []
                        nondir_clean_paths = []
                        for name in nondirs:
                            try:
                                clean, formatted = path_to_string(unicode_abspath(name))
                                nondir_clean_paths.append(clean)
                                nondir_formatted_paths.append(formatted)
                            except (UnicodeEncodeError, UnicodeDecodeError), e:
                                continue

                        if nondir_clean_paths:
                            columnize(nondir_clean_paths, nondir_formatted_paths)

                        if len(nondirs) == 0:
                            console_print(dirs[0] + u":")
                            print_directory(dirs[0])
                            dirs = dirs[1:]

                        for name in dirs:
                            console_print()
                            console_print(name + u":")
                            print_directory(name)

                except DropboxCommand.EOFError:
                    console_print(u"Dropbox daemon stopped.")
                except DropboxCommand.BadConnectionError, e:
                    console_print(u"Dropbox isn't responding!")
            else:
                if len(args) == 0:
                    args = [name for name in sorted(os.listdir(u"."), key=methodcaller('lower')) if type(name) == unicode]
                if len(args) == 0:
                    # Bail early if there's nothing to list to avoid crashing on indent below
                    console_print(u"<empty>")
                    return
                indent = max(len(st)+1 for st in args)
                for file in args:

                    try:
                        if type(file) is not unicode:
                            file = file.decode(enc)
                        fp = unicode_abspath(file)
                    except (UnicodeEncodeError, UnicodeDecodeError), e:
                        continue
                    if not os.path.exists(fp):
                        console_print(u"%-*s %s" % \
                                          (indent, file+':', "File doesn't exist"))
                        continue

                    try:
                        status = dc.icon_overlay_file_status(path=fp).get(u'status', [u'unknown'])[0]
                        console_print(u"%-*s %s" % (indent, file+':', status))
                    except DropboxCommand.CommandError, e:
                        console_print(u"%-*s %s" % (indent, file+':', e))
    except DropboxCommand.CouldntConnectError, e:
        console_print(u"Dropbox isn't running!")

@command
@requires_dropbox_running
def ls(args):
    u"""list directory contents with current sync status
dropbox ls [FILE]...

This is an alias for filestatus -l
"""
    return filestatus(["-l"] + args)

@command
@requires_dropbox_running
def puburl(args):
    u"""get public url of a file in your dropbox
dropbox puburl FILE

Prints out a public url for FILE.
"""
    if len(args) != 1:
        console_print(puburl.__doc__,linebreak=False)
        return

    try:
        with closing(DropboxCommand()) as dc:
            try:
                console_print(dc.get_public_link(path=unicode_abspath(args[0].decode(sys.getfilesystemencoding()))).get(u'link', [u'No Link'])[0])
            except DropboxCommand.CommandError, e:
                console_print(u"Couldn't get public url: " + str(e))
            except DropboxCommand.BadConnectionError, e:
                console_print(u"Dropbox isn't responding!")
            except DropboxCommand.EOFError:
                console_print(u"Dropbox daemon stopped.")
    except DropboxCommand.CouldntConnectError, e:
        console_print(u"Dropbox isn't running!")

@command
@requires_dropbox_running
def link(args):
    u"""get waiting for activation link of the dropboxd
dropbox link

Prints out the activation link of the Dropbox daemon.
"""

    status = open("/tmp/_dropbox.out", "r").readlines()[0:2]
    if len(status) > 0:
        link = "".join(status).rstrip("\n")
        console_print(u"%s" % link)
        return 5
    else:
	console_print(u"Auth link not found.")
        return 4

@command
@requires_dropbox_running
def status(args):
    u"""get current status of the dropboxd
dropbox status

Prints out the current status of the Dropbox daemon.
"""
    if len(args) != 0:
        console_print(status.__doc__,linebreak=False)
        return

    try:
        with closing(DropboxCommand()) as dc:
            try:
                lines = dc.get_dropbox_status()[u'status']
                if len(lines) == 0:
                    console_print(u'Idle')
                else:
                    for line in lines:
                        console_print(line)
                        if line.startswith(u"Waiting to be link"):
                            return 3
                return 1
            except KeyError:
                console_print(u"Couldn't get status: daemon isn't responding")
            except DropboxCommand.CommandError, e:
                console_print(u"Couldn't get status: " + str(e))
            except DropboxCommand.BadConnectionError, e:
                console_print(u"Dropbox isn't responding!")
            except DropboxCommand.EOFError:
                console_print(u"Dropbox daemon stopped.")
    except DropboxCommand.CouldntConnectError, e:
        console_print(u"Dropbox isn't running!")
    return 0

@command
def running(argv):
    u"""return whether dropbox is running
dropbox running

Returns 1 if running 0 if not running.
"""
    return int(is_dropbox_running())

@command
def installed(argv = ()):
    u"""return whether dropbox is installed
dropbox installed

Returns 1 if installed 0 if not installed.
"""
    db_path = os.path.expanduser(u"~/.dropbox-dist/dropboxd").encode(sys.getfilesystemencoding())
    return int(os.access(db_path, os.X_OK))

@command
@requires_dropbox_running
def stop(args):
    u"""stop dropboxd
dropbox stop

Stops the dropbox daemon.
"""
    try:
        with closing(DropboxCommand()) as dc:
            try:
                dc.tray_action_hard_exit()
            except DropboxCommand.BadConnectionError, e:
                console_print(u"Dropbox isn't responding!")
            except DropboxCommand.EOFError:
                console_print(u"Dropbox daemon stopped.")
            finally:
                open("/tmp/_dropbox.out", "w").write("")
    except DropboxCommand.CouldntConnectError, e:
        console_print(u"Dropbox isn't running!")

#returns true if link is necessary
def grab_link_url_if_necessary():
    try:
        with closing(DropboxCommand()) as dc:
            try:
                link_url = dc.needs_link().get(u"link_url", None)
                if link_url is not None:
                    console_print(u"To link this computer to a dropbox account, visit the following url:\n%s" % link_url[0])
                    return True
                else:
                    return False
            except DropboxCommand.CommandError, e:
                pass
            except DropboxCommand.BadConnectionError, e:
                console_print(u"Dropbox isn't responding!")
            except DropboxCommand.EOFError:
                console_print(u"Dropbox daemon stopped.")
    except DropboxCommand.CouldntConnectError, e:
        console_print(u"Dropbox isn't running!")

@command
@requires_dropbox_running
def lansync(argv):
    u"""enables or disables LAN sync
dropbox lansync [y/n]

options:
  y  dropbox will use LAN sync (default)
  n  dropbox will not use LAN sync
"""
    if len(argv) != 1:
        console_print(lansync.__doc__, linebreak=False)
        return

    s = argv[0].lower()
    if s.startswith('y') or s.startswith('-y'):
        should_lansync = True
    elif s.startswith('n') or s.startswith('-n'):
        should_lansync = False
    else:
        should_lansync = None

    if should_lansync is None:
        console_print(lansync.__doc__,linebreak=False)
    else:
        with closing(DropboxCommand()) as dc:
            dc.set_lan_sync(lansync='enabled' if should_lansync else 'disabled')


@command
@requires_dropbox_running
def exclude(args):
    u"""ignores/excludes a directory from syncing
dropbox exclude [list]
dropbox exclude add [DIRECTORY], [DIRECTORY] ...
dropbox exclude remove [DIRECTORY], [DIRECTORY] ...

"list" prints a list of directories currently excluded from syncing.
"add" adds one or more directories to the exclusion list, then resynchronizes Dropbox.
"remove" removes one or more directories from the exclusion list, then resynchronizes Dropbox.
With no arguments, executes "list".
Any specified path must be within Dropbox.
"""
    if len(args) == 0:
        try:
            with closing(DropboxCommand()) as dc:
                try:
                    lines = [relpath(path) for path in dc.get_ignore_set()[u'ignore_set']]
                    lines.sort()
                    if len(lines) == 0:
                        return 6
                    else:
                        for line in lines:
                            console_print(unicode(line))
                        return 7
                except KeyError:
                    console_print(u"Couldn't get ignore set: daemon isn't responding")
                except DropboxCommand.CommandError, e:
                    if e.args[0].startswith(u"No command exists by that name"):
                        console_print(u"This version of the client does not support this command.")
                    else:
                        console_print(u"Couldn't get ignore set: " + str(e))
                except DropboxCommand.BadConnectionError, e:
                    console_print(u"Dropbox isn't responding!")
                except DropboxCommand.EOFError:
                    console_print(u"Dropbox daemon stopped.")
        except DropboxCommand.CouldntConnectError, e:
            console_print(u"Dropbox isn't running!")
    elif len(args) == 1 and args[0] == u"list":
        exclude([])
    elif len(args) >= 2:
        sub_command = args[0]
        paths = args[1:]
        absolute_paths = []

        for path in paths:
          # Dropbox python script cant read paths with spaces in
          # it becuase of how it parses arguments from cli.
          # This replaces || with spaces in an effort to not modify 
          # the rest of the script to fix the bug.
          path = path.replace("||", " ")
          absolute_paths.append(unicode_abspath(path.decode(sys.getfilesystemencoding())))
        
        print absolute_paths

        if sub_command == u"add":
            try:
                with closing(DropboxCommand(timeout=None)) as dc:
                    try:
                        result = dc.ignore_set_add(paths=absolute_paths)
                        if result[u"ignored"]:
                            console_print(u"Excluded: ")
                            lines = [relpath(path) for path in result[u"ignored"]]
                            for line in lines:
                                console_print(unicode(line))
                            return 8
                    except KeyError:
                        console_print(u"Couldn't add ignore path: daemon isn't responding")
                    except DropboxCommand.CommandError, e:
                        if e.args[0].startswith(u"No command exists by that name"):
                            console_print(u"This version of the client does not support this command.")
                        else:
                            console_print(u"Couldn't get ignore set: " + str(e))
                    except DropboxCommand.BadConnectionError, e:
                        console_print(u"Dropbox isn't responding! [%s]" % e)
                    except DropboxCommand.EOFError:
                        console_print(u"Dropbox daemon stopped.")
            except DropboxCommand.CouldntConnectError, e:
                console_print(u"Dropbox isn't running!")
        elif sub_command == u"remove":
            try:
                with closing(DropboxCommand(timeout=None)) as dc:
                    try:
                        result = dc.ignore_set_remove(paths=absolute_paths)
                        if result[u"removed"]:
                            console_print(u"No longer excluded: ")
                            lines = [relpath(path) for path in result[u"removed"]]
                            for line in lines:
                                console_print(unicode(line))
                            return 8
                    except KeyError:
                        console_print(u"Couldn't remove ignore path: daemon isn't responding")
                    except DropboxCommand.CommandError, e:
                        if e.args[0].startswith(u"No command exists by that name"):
                            console_print(u"This version of the client does not support this command.")
                        else:
                            console_print(u"Couldn't get ignore set: " + str(e))
                    except DropboxCommand.BadConnectionError, e:
                        console_print(u"Dropbox isn't responding! [%s]" % e)
                    except DropboxCommand.EOFError:
                        console_print(u"Dropbox daemon stopped.")
            except DropboxCommand.CouldntConnectError, e:
                console_print(u"Dropbox isn't running!")
        else:
            console_print(exclude.__doc__, linebreak=False)
            return
    else:
        console_print(exclude.__doc__, linebreak=False)
        return

@command
def install(argv):
    u"""install dropboxd
dropbox install

Installs the dropbox daemon, dropboxd. If dropboxd is already installed, this will do nothing.
"""
    if installed() != 1:
        # install dropbox!!!
        try:
            download(writeLog = True)
        except:
            traceback.print_exc()
	else:
            console_print(u"Done!")
	    return 1
    else:
	console_print(u"Already installed, skipping.")
	return 1

@command
def start(argv):
    u"""start dropboxd
dropbox start [-i]

Starts the dropbox daemon, dropboxd. If dropboxd is already running, this will do nothing.

options:
  -i --install  auto install dropboxd if not available on the system
"""

    should_install = "-i" in argv or "--install" in argv

    # first check if dropbox is already running
    if is_dropbox_running():
        if not grab_link_url_if_necessary():
            console_print(u"Dropbox is already running!")
        return

    console_print(u"Starting Dropbox...", linebreak=False)
    console_flush()
    if not start_dropbox():
        if not should_install:
            console_print()
            console_print(u"The Dropbox daemon is not installed!")
            console_print(u"Run \"dropbox start -i\" to install the daemon")
            return

        # install dropbox!!!
        try:
            download()
        except:
            traceback.print_exc()
        else:
            if start_dropbox():
                if not grab_link_url_if_necessary():
                    console_print(u"Done!")
    else:
        if not grab_link_url_if_necessary():
            console_print(u"Done!")


def can_reroll_autostart():
    return u".config" in os.listdir(os.path.expanduser(u'~'))

def reroll_autostart(should_autostart):
    home_dir = os.path.expanduser(u'~')
    contents = os.listdir(home_dir)

    # UBUNTU
    if u".config" in contents:
        autostart_dir = os.path.join(home_dir, u".config", u"autostart")
        autostart_link = os.path.join(autostart_dir, u"dropbox.desktop")
        if should_autostart:
            if os.path.exists(DESKTOP_FILE):
                if not os.path.exists(autostart_dir):
                    os.makedirs(autostart_dir)
                shutil.copyfile(DESKTOP_FILE, autostart_link)
        elif os.path.exists(autostart_link):
            os.remove(autostart_link)



@command
def autostart(argv):
    u"""automatically start dropbox at login
dropbox autostart [y/n]

options:
  n  dropbox will not start automatically at login
  y  dropbox will start automatically at login (default)

Note: May only work on current Ubuntu distributions.
"""
    if len(argv) != 1:
        console_print(''.join(autostart.__doc__.split('\n', 1)[1:]).decode('ascii'))
        return

    s = argv[0].lower()
    if s.startswith('y') or s.startswith('-y'):
        should_autostart = True
    elif s.startswith('n') or s.startswith('-n'):
        should_autostart = False
    else:
        should_autostart = None

    if should_autostart is None:
        console_print(autostart.__doc__,linebreak=False)
    else:
        reroll_autostart(should_autostart)

@command
def help(argv):
    u"""provide help
dropbox help [COMMAND]

With no arguments, print a list of commands and a short description of each. With a command, print descriptive help on how to use the command.
"""
    if not argv:
        return usage(argv)
    for command in commands:
        if command == argv[0]:
            console_print(commands[command].__doc__.split('\n', 1)[1].decode('ascii'))
            return
    for alias in aliases:
        if alias == argv[0]:
            console_print(aliases[alias].__doc__.split('\n', 1)[1].decode('ascii'))
            return
    console_print(u"unknown command '%s'" % argv[0], f=sys.stderr)

def usage(argv):
    console_print(u"Dropbox command-line interface\n")
    console_print(u"commands:\n")
    console_print(u"Note: use dropbox help <command> to view usage for a specific command.\n")
    out = []
    for command in commands:
        out.append((command, commands[command].__doc__.splitlines()[0]))
    spacing = max(len(o[0])+3 for o in out)
    for o in out:
        console_print(" %-*s%s" % (spacing, o[0], o[1]))
    console_print()

def main(argv):
    global commands

    # now we need to find out if one of the commands are in the
    # argv list, and if so split the list at the point to
    # separate the argv list at that point
    cut = None
    for i in range(len(argv)):
        if argv[i] in commands or argv[i] in aliases:
            cut = i
            break

    if cut == None:
        usage(argv)
        os._exit(0)
        return

    # lol no options for now
    globaloptionparser = optparse.OptionParser()
    globaloptionparser.parse_args(argv[0:i])

    # now dispatch and run
    result = None
    if argv[i] in commands:
        result = commands[argv[i]](argv[i+1:])
    elif argv[i] in aliases:
        result = aliases[argv[i]](argv[i+1:])

    # flush, in case output is rerouted to a file.
    console_flush()

    # done
    return result

if __name__ == "__main__":
    ret = main(sys.argv)
    if ret is not None:
        sys.exit(ret)
