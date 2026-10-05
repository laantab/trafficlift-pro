"""Explicit project-local research configuration; no external calls or key logging."""
import os
import re
import threading
from pathlib import Path
from dotenv import dotenv_values

VERSION = '4.7'
ROOT = Path(__file__).resolve().parents[1]
_LOCK = threading.Lock()
_SOURCE = 'environment'


def load_research_config(root=None):
    global _SOURCE
    path = Path(root or ROOT) / '.env'
    values = dotenv_values(path, encoding='utf-8-sig') if path.is_file() else {}
    file_key = str(values.get('TAVILY_API_KEY') or '').strip()
    current = os.environ.get('TAVILY_API_KEY', '').strip()
    # The local studio's explicit file must win over stale inherited values.
    # Production platform credentials retain precedence.
    if file_key and (os.environ.get('TRAFFICLIFT_FREE_VIDEO') == '1' or not current):
        os.environ['TAVILY_API_KEY'] = file_key
        _SOURCE = 'project_file'
    return research_status()


def research_status():
    configured = bool(os.environ.get('TAVILY_API_KEY','').strip())
    return dict(version=VERSION, configured=configured,
                provider='tavily' if configured else 'not_connected',
                source=_SOURCE if configured else 'none')


def save_research_key(key, root=None):
    global _SOURCE
    key = str(key).strip()
    if not re.fullmatch(r'tvly-[A-Za-z0-9_-]{10,200}', key):
        raise ValueError('Paste the complete Tavily API key copied from your account.')
    path = Path(root or ROOT) / '.env'
    with _LOCK:
        original = path.read_text(encoding='utf-8-sig') if path.is_file() else ''
        cleaned = re.sub(r'(?m)^\s*TAVILY_API_KEY\s*=.*(?:\r?\n|$)', '', original)
        updated = cleaned.rstrip() + '\nTAVILY_API_KEY=' + key + '\n'
        temp = path.with_name('.env.research.tmp')
        try:
            temp.write_text(updated,encoding='utf-8')
            temp.chmod(0o600)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        os.environ['TAVILY_API_KEY'] = key
        _SOURCE = 'project_file'
    return research_status()
