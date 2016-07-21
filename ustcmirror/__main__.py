#!/usr/bin/python -O
# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals, with_statement

import os
import sys
import pwd
import sqlite3
from os import path
import logging
import argparse
import shlex
import shutil
import tempfile
import traceback
import subprocess
try:
    from subprocess import DEVNULL
except ImportError:
    DEVNULL = open(os.devnull, 'wb')

from .config import load_user_config
_USER_CFG = load_user_config()
BIN_PATH = _USER_CFG['BIN_PATH']
SYNC_USR = _USER_CFG['SYNC_USR']
REPO_DIR = _USER_CFG['REPO_DIR']
LOG_DIR = _USER_CFG['LOG_DIR']
ETC_DIR = _USER_CFG['ETC_DIR']
BIND_ADDR = _USER_CFG['BIND_ADDR']
DB_PATH = _USER_CFG['DB_PATH']

from .utils import DbDict

class CustomFormatter(argparse.HelpFormatter):

    def _format_action_invocation(self, action):
        if not action.option_strings:
            metavar, = self._metavar_formatter(action, action.dest)(1)
            return metavar
        else:
            # if the Optional doesn't take a value, format is:
            #    -s, --long
            if action.nargs == 0:
                return ', '.join(action.option_strings)
            # if the Optional takes a value, format is:
            #    -s, --long ARGS
            else:
                default = action.dest.upper()
                args_string = self._format_args(action, default)
                option_string = ', '.join(action.option_strings)
            return '{} {}'.format(option_string, args_string)

    def _get_help_string(self, action):
        help = action.help
        if '%(default)' not in action.help and action.default is not None:
            if action.default is not argparse.SUPPRESS:
                defaulting_nargs = [argparse.OPTIONAL, argparse.ZERO_OR_MORE]
                if action.option_strings or action.nargs in defaulting_nargs:
                    help += ' (default: %(default)s)'
        return help

if not hasattr(__builtins__, 'NotADirectoryError'):
    class NotADirectoryError(Exception):
        pass

class UserNotFound(Exception):
    pass

class MissingSyncMethod(Exception):
    pass


def try_mkdir(d):
    if not path.isdir(d):
        if not path.exists(d):
            os.makedirs(d)
        else:
            raise NotADirectoryError(d)


class Manager(object):

    def __init__(self, verbose=False):

        if verbose:
            level = logging.DEBUG
        else:
            level = logging.INFO
        self._log = logging.getLogger(__name__)
        self._log.setLevel(level)
        ch = logging.StreamHandler()
        fmter = logging.Formatter(
            '[%(asctime)s] %(levelname)s: %(message)s')
        ch.setFormatter(fmter)
        self._log.addHandler(ch)

        try:
            self._pw = pwd.getpwnam(SYNC_USR)
        except KeyError:
            raise UserNotFound(SYNC_USR)

        self._db = DbDict(self._init_db(DB_PATH))

    def add(self, name, prog, args, interval):

        repo = path.join(REPO_DIR, name)
        log = path.join(LOG_DIR, name.lower())
        try_mkdir(repo)
        try_mkdir(log)

        self._db[name] = (prog, args)

        tab = subprocess.check_output(['crontab', '-l'])
        fd, p = tempfile.mkstemp()
        try:
            with os.fdopen(fd, 'wb') as tmp:
                tmp.write(tab)
                tmp.write('{} {} sync {name}\n'.format(interval, BIN_PATH, name=name).encode('utf-8'))
            subprocess.check_call(['crontab', p])
        except:
            self._log.warn('Error occurred:')
            traceback.print_exc()
        finally:
            os.remove(p)

    def sync(self, name):

        repo = path.join(REPO_DIR, name)
        if not path.isdir(repo):
            raise NotADirectoryError(repo)
        log = path.join(LOG_DIR, name.lower())
        # Otherwise may be created by root
        try_mkdir(log)

        prog, args = self._db[name]

        if prog == 'ustcsync':
            uid = self._pw.pw_uid
            gid = self._pw.pw_gid
            ct = 'docker run --rm -v {conf}:/opt/ustcsync/etc:ro -v {repo}:/srv/repo/{name} -v {log}:/opt/ustcsync/log/{name} -e BIND_ADDRESS={bind_ip} -u {uid}:{gid} --name syncing-{name} --net=host ustclug/mirror:latest {args}'.format(
                    name=name, conf=ETC_DIR, repo=repo, log=log, bind_ip=BIND_ADDR, uid=uid, gid=gid, args=args)
            cmd = shlex.split(ct)
            self._log.debug('Command: %s', cmd)
        else:
            cmd = shlex.split('{} {}'.format(prog, args))
        subprocess.Popen(cmd, stdout=DEVNULL, stderr=DEVNULL)

    def stop(self, name, timeout=60):

        args = 'docker stop -t {timeout} syncing-{name}'.format(
            timeout=timeout, name=name)
        cmd = shlex.split(args)
        self._log.debug('Command: %s', cmd)
        retcode = subprocess.call(cmd)
        self._log.debug('Docker return: %s', retcode)

    def list(self):

        for item in self._db:
            name, prog, args = item
            print(name, prog)

    def remove(self, name):

        try:
            shutil.rmtree(path.join(LOG_DIR, name))
        except:
            traceback.print_exc()

        tab = subprocess.check_output(['crontab', '-l']).decode('utf-8').splitlines()
        fd, p = tempfile.mkstemp()
        try:
            with os.fdopen(fd, 'w') as tmp:
                for l in tab:
                    s = l.strip()
                    if s.startswith('#') or not s.endswith(name):
                        tmp.write(l + '\n')
            subprocess.check_call(['crontab', p])
        except:
            traceback.print_exc()
        finally:
            os.remove(p)

    def _init_db(self, f):

        conn = sqlite3.connect(f)
        cursor = conn.cursor()
        cursor.execute("""CREATE TABLE IF NOT EXISTS repositories (
                          name TEXT primary key,
                          program TEXT,
                          args TEXT);""")
        cursor.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uniq_repo on repositories(name);""")
        conn.commit()
        return conn

    def __enter__(self):

        return self

    def __exit__(self, exc_type, exc_value, traceback):

        self._db.close()
        return False


def main():

    parser = argparse.ArgumentParser(
        prog='ustcmirror',
        formatter_class=CustomFormatter)

    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        default=False)

    subparsers = parser.add_subparsers(
        help='Available commands', dest='command')

    subparsers.add_parser('init',
                          formatter_class=CustomFormatter,
                          help='Initialize environment')

    add_pser = subparsers.add_parser('add',
                                     formatter_class=CustomFormatter,
                                     help='Add a new repository')
    add_pser.add_argument(
        '-p',
        '--program',
        default='ustcsync',
        help='Sync method')
    add_pser.add_argument(
        '-a',
        '--args',
        default='',
        help='Arguments passed to program')
    add_pser.add_argument(
        '-i',
        '--interval',
        default='@hourly',
        help='Sync interval')
    add_pser.add_argument('name')

    sync_pser = subparsers.add_parser('sync',
                                      formatter_class=CustomFormatter,
                                      help='Start container to sync')
    sync_pser.add_argument('name')

    stop_pser = subparsers.add_parser('stop',
                                      formatter_class=CustomFormatter,
                                      help='Stop container')
    stop_pser.add_argument(
        '-t',
        '--timeout',
        default='60')
    stop_pser.add_argument('name')

    subparsers.add_parser('list',
                          formatter_class=CustomFormatter,
                          help='List repositories')

    rm_pser = subparsers.add_parser('remove', help='Remove repository')
    rm_pser.add_argument('name')

    if len(sys.argv) > 1:
        args = parser.parse_args()
    else:
        parser.print_help()
        parser.exit(1)

    args_dict = vars(args)
    get = args_dict.get

    with Manager(get('verbose')) as manager:
        if get('command') == 'add':
            if get('program') == 'ustcsync':
                if not get('args'):
                    args = get('name')
                else:
                    args = get('args')
            else:
                args = get('args') or ''
            manager.add(get('name'), get('program'), args, get('interval'))
        elif get('command') == 'sync':
            manager.sync(get('name'))
        elif get('command') == 'stop':
            manager.stop(get('name'), get('timeout'))
        elif get('command') == 'list':
            manager.list()
        elif get('command') == 'remove':
            manager.remove(get('name'))

if __name__ == '__main__':
    main()
