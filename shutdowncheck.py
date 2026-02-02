#!/usr/bin/python3

import subprocess
import logging
import sys
import datetime
import configparser
import glob
from systemd.journal import JournalHandler

CONFIG_FILE = "autoshutdown.conf"

# ------------------------------------------------------------
# Low-level checks
# ------------------------------------------------------------

def serviceActive(iPort, sName, iMaxIdle):
    result = subprocess.run(
        f'/bin/netstat --tcp --numeric | grep --count ":{iPort}"',
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True
    )

    iOut = int(result.stdout.strip()) if result.stdout else 0
    bActive = (iOut > iMaxIdle)

    log.info("%s: found=%d maxIdle=%d active=%d",
             sName, iOut, iMaxIdle, bActive)
    return bActive



def sambaActive(exclude_ips=None):
    if exclude_ips is None:
        exclude_ips = []

    try:
        sOut = subprocess.check_output(
            '/bin/netstat --tcp --numeric | grep ":445"',
            shell=True
        ).decode()

        for line in sOut.splitlines():
            if not any(ip in line for ip in exclude_ips):
                log.info("samba: active connection [%s]", line)
                return True

        log.info("samba: no relevant connections")
    except subprocess.CalledProcessError:
        log.info("samba: inactive")

    return False


def psActive(process_names, iMaxIdle):
    if isinstance(process_names, str):
        process_names = [process_names]

    for name in process_names:
        try:
            sOut = subprocess.check_output(
                f'/usr/bin/pgrep {name}', shell=True
            )
            iCnt = sOut.decode().count('\n')
            bActive = (iCnt > iMaxIdle)
            log.info("process %s: found=%d maxIdle=%d active=%d",
                     name, iCnt, iMaxIdle, bActive)
            if bActive:
                return True
        except subprocess.CalledProcessError:
            log.info("process %s: inactive", name)

    return False


def vdrRecording(pattern="/tmp/vdrec_*"):
    files = glob.glob(pattern)
    if files:
        log.info("vdrRecording: found %s", files)
        return True
    log.info("vdrRecording: inactive")
    return False


def vdrGetTimerList():
    dates = []
    with open('/var/lib/vdr/timers.conf') as f:
        for line in f:
            parts = line.split(':')
            dt = int(datetime.datetime.strptime(
                parts[2] + '_' + parts[3],
                '%Y-%m-%d_%H%M'
            ).timestamp())
            dates.append(dt)
    return sorted(dates)


def vdrTimerVeryClose(iClose):
    now = datetime.datetime.now().timestamp()
    for d in vdrGetTimerList():
        delta = d - now
        if now < d and delta <= iClose:
            log.info("vdrTimer: timer %u close (%d sec)", d, delta)
            return True
    log.info("vdrTimer: no close timer")
    return False


def minUptime(iMinSeconds):
    with open('/proc/uptime') as f:
        up = int(float(f.readline().split()[0]))
    bActive = (up < iMinSeconds)
    log.info("uptime: up=%d min=%d active=%d",
             up, iMinSeconds, bActive)
    return bActive


def is_container_running(container_names):
    if isinstance(container_names, str):
        container_names = [container_names]

    try:
        result = subprocess.run(
            ['/usr/sbin/pct', 'list'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:
            log.info("container: pct list failed")
            return False

        for line in result.stdout.splitlines():
            for name in container_names:
                if name in line and "running" in line:
                    log.info("container %s: running", name)
                    return True

        log.info("container: none running")
    except Exception as e:
        log.info("container check error: %s", e)

    return False


# ------------------------------------------------------------
# Config helpers
# ------------------------------------------------------------

def load_config():
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_FILE)
    return cfg


def cfg_bool(cfg, sec, key, default=False):
    return cfg.getboolean(sec, key, fallback=default)


def cfg_int(cfg, sec, key, default=0):
    return cfg.getint(sec, key, fallback=default)


def cfg_list(cfg, sec, key):
    if not cfg.has_option(sec, key):
        return []
    return [x.strip() for x in cfg.get(sec, key).split(',') if x.strip()]


# ------------------------------------------------------------
# Wrapper checks
# ------------------------------------------------------------

def check_service(cfg, section, name, d_port, d_idle, d_enable=True):
    if not cfg_bool(cfg, section, 'enable', d_enable):
        log.info("%s: disabled by config", name)
        return False
    port = cfg_int(cfg, section, 'port', d_port)
    idle = cfg_int(cfg, section, 'maxidle', d_idle)
    return serviceActive(port, name, idle)


def check_samba(cfg):
    if not cfg_bool(cfg, 'samba', 'enable', False):
        return False
    exclude = cfg_list(cfg, 'samba', 'exclude_ips')
    return sambaActive(exclude)


def check_processes(cfg):
    if not cfg_bool(cfg, 'processes', 'enable', False):
        return False
    names = cfg_list(cfg, 'processes', 'names')
    idle = cfg_int(cfg, 'processes', 'maxidle', 0)
    return psActive(names, idle)


def check_vdr_recording(cfg):
    if not cfg_bool(cfg, 'vdr_recording', 'enable', False):
        return False
    pattern = cfg.get('vdr_recording', 'pattern', fallback='/tmp/vdrec_*')
    return vdrRecording(pattern)


def check_vdr_timer(cfg):
    if not cfg_bool(cfg, 'vdr_timer', 'enable', False):
        return False
    close = cfg_int(cfg, 'vdr_timer', 'close_seconds', 1200)
    return vdrTimerVeryClose(close)


def check_uptime(cfg):
    if not cfg_bool(cfg, 'uptime', 'enable', True):
        return False
    sec = cfg_int(cfg, 'uptime', 'minseconds', 1800)
    return minUptime(sec)


def check_container(cfg):
    if not cfg_bool(cfg, 'container', 'enable', False):
        return False
    names = cfg_list(cfg, 'container', 'names')
    return is_container_running(names)


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def printHelp():
    print(f"usage {sys.argv[0]} --shutdowncheck")


log = logging.getLogger('autoshutdown')
log.addHandler(JournalHandler())
log.setLevel(logging.INFO)

cfg = load_config()

if len(sys.argv) != 2:
    printHelp()
    sys.exit(1)

if sys.argv[1] == '--shutdowncheck':
    bActive = False

    bActive |= check_service(cfg, 'ssh', 'ssh', 22, 0, d_enable=False)
    bActive |= check_service(cfg, 'vnsiserver', 'vnsiserver', 34890, 1)
    bActive |= check_service(cfg, 'streamdevserver', 'streamdevserver', 3000, 0)

    bActive |= check_samba(cfg)
    bActive |= check_processes(cfg)
    bActive |= check_vdr_recording(cfg)
    bActive |= check_vdr_timer(cfg)
    bActive |= check_uptime(cfg)
    bActive |= check_container(cfg)

    if bActive:
        log.info("Ready shutdown: no")
        sys.exit(1)
    else:
        log.info("Ready shutdown: yes")
        subprocess.run("/sbin/shutdown -h now", shell=True)
        sys.exit(0)

else:
    printHelp()
    sys.exit(1)
