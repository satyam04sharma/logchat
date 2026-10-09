"""Discover user-owned listeners without probing HTTP, reading argv or environment.

psutil process connections API: https://psutil.io/api/process.html
Discovery identifies a candidate application; it cannot expose that application's stdout.
"""
import os
from pathlib import Path

import psutil

from pipeline.redaction import redact_text


def discover(exclude_port=None):
    projects=[]; seen=set(); skipped=0
    for process in psutil.process_iter(['pid','name']):
        try:
            if process.pid==os.getpid():
                continue
            if hasattr(os,'getuid') and process.uids().real!=os.getuid():
                continue
            connections=process.net_connections(kind='inet')
            listeners=[connection for connection in connections if connection.status==psutil.CONN_LISTEN and connection.laddr]
            if not listeners:
                continue
            try: directory=process.cwd()
            except (psutil.Error,OSError): directory=None
            for connection in listeners:
                address,port=connection.laddr.ip,connection.laddr.port
                if address not in {'127.0.0.1','::1','0.0.0.0','::'} or port==exclude_port:
                    continue
                key=(process.pid,port)
                if key in seen: continue
                seen.add(key)
                projects.append({'pid':process.pid,'port':port,'process':redact_text(process.info.get('name') or 'unknown')[:100],
                                 'path':redact_text(directory) if directory else None,
                                 'name':redact_text(Path(directory).name) if directory else 'Listening application',
                                 'address':address,'logging_connected':False})
        except (psutil.Error,OSError):
            skipped+=1
    return {'projects':sorted(projects,key=lambda row:(row['port'],row['pid'])),
            'notice':'Visible user-owned listening processes only; OS permissions can hide others. Discovery does not capture logs. Use logchat local run, or add structured event delivery.',
            'skipped_processes':skipped}
