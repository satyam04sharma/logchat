"""Internal native sidecar entry point; no Docker or frontend compiler required."""
import argparse
from pathlib import Path
import uvicorn


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--state-dir',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    from logchat.local.lifecycle import ensure_fresh_rag
    from logchat.local.lifecycle import state_directory
    ensure_fresh_rag(state_directory(args.state_dir))
    from logchat.local.app import create_app
    uvicorn.run(create_app(args.state_dir,args.port),host='127.0.0.1',port=args.port,
                access_log=False,log_level='critical')

if __name__=='__main__':main()
