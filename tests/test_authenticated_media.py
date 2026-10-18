#!/usr/bin/env python3
"""Exercise a real, disposable Tuwunel against a private S3 protocol fixture.

Uses synthetic credentials and loopback only. No production Matrix/B2 access.
"""

import argparse
import contextlib
import hashlib
import hmac
import http.server
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


HTTP = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))


def request(url, *, method="GET", data=None, token=None, headers=None):
    headers = dict(headers or {})
    if token:
        headers["Authorization"] = "Bearer " + token
    if isinstance(data, dict):
        data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        response = HTTP.open(req, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


class S3(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    objects = {}
    multipart = {}
    calls = []

    def log_message(self, *_args):
        pass

    def respond(self, status, body=b"", headers=None, head=False):
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def parts(self):
        parsed = urllib.parse.urlsplit(self.path)
        return parsed.path, urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    def read_body(self):
        assert "Transfer-Encoding" not in self.headers, "unexpected chunked request"
        return self.rfile.read(int(self.headers.get("Content-Length", "0")))

    def authorize(self):
        path, query = self.parts()
        signed = "X-Amz-Signature" in query
        bearer = self.headers.get("Authorization", "")
        assert not bearer.startswith("Bearer "), "Matrix token leaked to object storage"
        assert signed or bearer.startswith("AWS4-HMAC-SHA256 "), "unsigned S3 access"
        if signed:
            assert query["X-Amz-Expires"] == ["300"]
            assert query["X-Amz-SignedHeaders"] == ["host"]
            assert query["X-Amz-Credential"][0].startswith("fixture-key/")
            # Independently verify the actual SigV4 capability, including the
            # bucket and object path, rather than accepting any URL with a signature.
            scope = query["X-Amz-Credential"][0].split("/", 1)[1]
            stamp = query["X-Amz-Date"][0]
            quote = lambda value: urllib.parse.quote(value, safe="-_.~")
            canonical_query = "&".join(
                sorted(f"{quote(key)}={quote(value)}" for key, values in query.items()
                       if key != "X-Amz-Signature" for value in values)
            )
            canonical = "\n".join([
                self.command, path, canonical_query,
                f"host:{self.headers['Host']}\n", "host", "UNSIGNED-PAYLOAD",
            ])
            to_sign = "\n".join([
                "AWS4-HMAC-SHA256", stamp, scope,
                hashlib.sha256(canonical.encode()).hexdigest(),
            ])
            key = b"AWS4fixture-secret"
            for component in scope.split("/"):
                key = hmac.new(key, component.encode(), hashlib.sha256).digest()
            expected = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
            assert hmac.compare_digest(expected, query["X-Amz-Signature"][0])
        self.calls.append((self.command, path, signed))
        return path, query

    def do_PUT(self):
        path, query = self.authorize()
        body = self.read_body()
        if "uploadId" in query:
            self.multipart[query["uploadId"][0]]["parts"][int(query["partNumber"][0])] = body
        else:
            self.objects[path] = (body, self.object_headers())
        self.respond(200, headers={"ETag": '"fixture-etag"'})

    def object_headers(self):
        return {key: self.headers[key] for key in ["Content-Type", "Content-Disposition"]
                if key in self.headers}

    def do_POST(self):
        path, query = self.authorize()
        body = self.read_body()
        if "uploads" in query:
            upload_id = str(len(self.multipart) + 1)
            self.multipart[upload_id] = {"parts": {}, "headers": self.object_headers()}
            xml = (f"<InitiateMultipartUploadResult><Bucket>media</Bucket><Key>{path}</Key>"
                   f"<UploadId>{upload_id}</UploadId></InitiateMultipartUploadResult>")
        else:
            upload = self.multipart.pop(query["uploadId"][0])
            ET.fromstring(body)
            content = b"".join(value for _, value in sorted(upload["parts"].items()))
            self.objects[path] = (content, upload["headers"])
            xml = ('<CompleteMultipartUploadResult><Location>http://localhost/</Location>'
                   f'<Bucket>media</Bucket><Key>{path}</Key><ETag>"fixture-etag"</ETag>'
                   '</CompleteMultipartUploadResult>')
        self.respond(200, xml.encode(), {"Content-Type": "application/xml"})

    def do_GET(self):
        self.get_object(False)

    def do_HEAD(self):
        self.get_object(True)

    def get_object(self, head):
        path, _query = self.authorize()
        if path not in self.objects:
            self.respond(404, head=head)
            return
        body, headers = self.objects[path]
        self.respond(200, body, {
            **headers, "ETag": '"fixture-etag"',
            "Last-Modified": "Sat, 17 Oct 2026 00:00:00 GMT",
        }, head=head)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def homeserver(binary, root, endpoint, *, redirects=True, strict=False):
    port = free_port()
    config = root / "tuwunel.toml"
    config.write_text(f'''[global]
server_name = "redirect.test"
database_path = {json.dumps(str(root / "db"))}
address = ["127.0.0.1"]
port = [{port}]
allow_registration = false
allow_guest_registration = false
allow_federation = false
allow_legacy_media = false
registration_shared_secret = "fixture-registration-secret"
trusted_servers = []
# All peers are numeric loopback addresses; avoid the sandbox's absent resolv.conf.
dns_servers = ["127.0.0.1"]
max_request_size = 52428800
rocksdb_allow_fallocate = false
media_allow_redirect = {str(redirects).lower()}
media_deny_framing = {str(strict).lower()}
media_storage_providers = ["s3", "media"]
store_media_on_providers = ["s3", "media"]
log = "error"
[global.storage_provider.media.local]
base_path = {json.dumps(str(root / "media"))}
create_if_missing = true
[global.storage_provider.s3.s3]
endpoint = {json.dumps(endpoint)}
bucket = "media"
region = "fixture-region"
key = "fixture-key"
secret = "fixture-secret"
use_https = false
use_vhost_request = false
use_payload_signatures = false
startup_check = false
multipart_threshold = "1 MiB"
''')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("TUWUNEL_", "CONDUIT_", "CONDUWUIT_"))}
    env["TUWUNEL_CONFIG"] = str(config)
    with (root / "server.log").open("w+") as log:
        process = subprocess.Popen([binary], stdout=log, stderr=subprocess.STDOUT, env=env)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(300):
                if process.poll() is not None:
                    raise RuntimeError(f"Tuwunel exited with {process.returncode}")
                try:
                    if request(base + "/_matrix/client/versions")[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Tuwunel readiness timed out")
            yield base
        except BaseException:
            log.seek(0)
            print(log.read()[-12000:])
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def register(base):
    status, _headers, body = request(base + "/_synapse/admin/v1/register")
    assert status == 200, body
    nonce = json.loads(body)["nonce"]
    message = b"\0".join([nonce.encode(), b"fixture", b"fixture-password", b"admin"])
    mac = hmac.new(b"fixture-registration-secret", message, hashlib.sha1).hexdigest()
    status, _headers, body = request(base + "/_synapse/admin/v1/register", method="POST", data={
        "nonce": nonce, "username": "fixture", "password": "fixture-password",
        "admin": True, "mac": mac,
    })
    assert status == 200, body
    return json.loads(body)["access_token"]


def upload(base, token, payload, name="original.txt", content_type="text/plain"):
    status, _headers, body = request(
        base + "/_matrix/media/v3/upload?filename=" + urllib.parse.quote(name),
        method="POST", data=payload, token=token, headers={"Content-Type": content_type},
    )
    assert status == 200, body
    mxc = json.loads(body)["content_uri"]
    media_id = mxc.rsplit("/", 1)[1]
    return base + "/_matrix/client/v1/media/download/redirect.test/" + media_id


def direct(url, token, expected):
    before = len([x for x in S3.calls if x[0] == "GET"])
    status, headers, body = request(url, token=token)
    assert status == 307, (status, body)
    assert not body
    assert headers["Cache-Control"] == "private, no-store"
    assert "authorization" in headers["Vary"].lower()
    location = headers["Location"]
    assert "access_token" not in location and token not in location
    assert len([x for x in S3.calls if x[0] == "GET"]) == before, "server proxied the body"
    status, final_headers, body = request(location)
    assert status == 200 and body == expected
    return urllib.parse.urlsplit(location).path, final_headers


def png():
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
    pixels = b"".join(b"\x00" + b"\xff\x00\x00" * 128 for _ in range(128))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", 128, 128, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def exercise(binary):
    with tempfile.TemporaryDirectory(prefix="tuwunel-redirect-") as directory:
        root = Path(directory)
        fixture = http.server.ThreadingHTTPServer(("127.0.0.1", 0), S3)
        thread = threading.Thread(target=fixture.serve_forever, daemon=True)
        thread.start()
        endpoint = f"http://127.0.0.1:{fixture.server_port}"
        try:
            with homeserver(binary, root, endpoint) as base:
                token = register(base)
                payload = b"authenticated direct-download regression\n"
                url = upload(base, token, payload)
                for suffix in ["", "/original.txt", "/changed.txt"]:
                    before = len(S3.calls)
                    status, headers, _body = request(url + suffix)
                    assert status == 401 and "Location" not in headers
                    assert len(S3.calls) == before, "unauthenticated request reached storage"
                path, headers = direct(url, token, payload)
                assert headers["Content-Type"] == "text/plain"
                assert "original.txt" in headers["Content-Disposition"]
                direct(url + "/original.txt", token, payload)
                unstable = url.replace("/_matrix/client/v1/media", "/_matrix/client/unstable/org.matrix.msc3916/media")
                direct(unstable, token, payload)
                status, headers, body = request(url + "/changed.txt", token=token)
                assert status == 200 and body == payload
                assert "changed.txt" in headers["Content-Disposition"]
                legacy = url.replace("/_matrix/client/v1/media", "/_matrix/media/v3")
                assert request(legacy)[0] == 403, "legacy media became accessible"

                # Existing objects without Matrix headers continue through the proxy.
                old = S3.objects[path]
                S3.objects[path] = (old[0], {"Content-Type": "application/octet-stream"})
                assert request(url, token=token)[::2] == (200, payload)
                # A missing S3 object must fall back to the intact local copy.
                del S3.objects[path]
                assert request(url, token=token)[::2] == (200, payload)
                S3.objects[path] = old

                html = b"<script>alert('fixture')</script>"
                html_url = upload(base, token, html, "unsafe.html", "text/html")
                _path, headers = direct(html_url, token, html)
                assert headers["Content-Disposition"].startswith("attachment")

                large = b"multipart fixture\n" * 80000
                large_url = upload(base, token, large, "large.txt")
                _path, headers = direct(large_url, token, large)
                assert "large.txt" in headers["Content-Disposition"]
                assert any(method == "POST" for method, _path, _signed in S3.calls)

                picture = png()
                image_url = upload(base, token, picture, "image.png", "image/png")
                thumbnail = image_url.replace("/download/", "/thumbnail/") + "?width=32&height=32&method=scale"
                before = len(S3.calls)
                status, headers, _body = request(thumbnail)
                assert status == 401 and "Location" not in headers and len(S3.calls) == before
                status, headers, body = request(thumbnail, token=token)
                assert status == 307, (status, body)
                status, headers, thumbnail_bytes = request(headers["Location"])
                assert status == 200 and headers["Content-Type"].startswith("image/")
                direct(thumbnail, token, thumbnail_bytes)
                direct(thumbnail + "&animated=false", token, thumbnail_bytes)
                direct(thumbnail.replace("width=32&height=32", "width=16&height=16"), token, thumbnail_bytes)
                suffix = url.split(base, 1)[1]

            for redirects, strict in [(False, False), (True, True)]:
                with homeserver(binary, root, endpoint, redirects=redirects, strict=strict) as base:
                    status, headers, body = request(base + suffix, token=token)
                    assert status == 200 and body == payload and "Location" not in headers
                    if strict:
                        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        finally:
            fixture.shutdown()
            fixture.server_close()
            thread.join(timeout=5)
    print("PASS: authenticated direct downloads, signatures, headers, thumbnails, multipart, fallbacks and disabled/strict policy")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", help="path to the patched tuwunel executable")
    exercise(parser.parse_args().binary)
