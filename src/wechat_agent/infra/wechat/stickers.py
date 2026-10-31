"""Bounded downloads of WeChat CDN stickers, never arbitrary model URLs."""
import hashlib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Sticker CDN redirect refused')

def receive_sticker(content, directory):
    if len(content) > 100000 or '<!DOCTYPE' in content.upper():
        raise ValueError('Invalid sticker metadata')
    node = ET.fromstring(content).find('.//emoji')
    if node is None:
        raise ValueError('No sticker metadata')
    url = node.get('cdnurl') or node.get('thumburl')
    if not url:
        raise ValueError('Sticker has no accessible CDN URL')
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme not in ('https', 'http') or parsed.username or parsed.password or parsed.port not in (None,80,443):
        raise ValueError('Invalid CDN address')
    if not any(host.endswith('.'+domain) for domain in ('qpic.cn','weixin.qq.com','qq.com','qlogo.cn')):
        raise ValueError('Unsupported sticker CDN')
    with urllib.request.build_opener(NoRedirect).open(url, timeout=20) as response:
        data = response.read(10*1024*1024+1)
    if len(data)>10*1024*1024:
        raise ValueError('Sticker too large')
    import io
    with Image.open(io.BytesIO(data)) as image:
        if image.width*image.height>16000000:
            raise ValueError('Sticker dimensions too large')
        image.seek(0)
        directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
        path=directory/(hashlib.sha256(data).hexdigest()[:24]+'.png')
        image.convert('RGB').save(path)
    return path.resolve()
