"""Disposable Registry proxy slows blobs so Docker emits intermediate counters."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import time

class Proxy(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def send_error(self,*args,**kwargs):
        # A TLS probe on this loopback-only fixture must receive an HTTP status
        # line so Docker recognizes the normal HTTPS-to-HTTP fallback.
        if self.request_version=='HTTP/0.9':self.request_version='HTTP/1.1'
        super().send_error(*args,**kwargs)
    def do_HEAD(self):self.forward('HEAD')
    def do_GET(self):self.forward('GET')
    def forward(self,method):
        try:response=urlopen(Request('http://fixture-registry:5000'+self.path,method=method),timeout=10)
        except HTTPError as error:response=error
        with response:
            self.send_response(response.status)
            for key,value in response.headers.items():
                if key.lower() not in ('connection','transfer-encoding','server','date'):self.send_header(key,value)
            self.end_headers()
            if method=='HEAD':return
            while chunk:=response.read(65536):
                self.wfile.write(chunk);self.wfile.flush()
                if '/blobs/' in self.path:time.sleep(.03)
    def log_message(self,*args):pass

if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',8080),Proxy).serve_forever()
