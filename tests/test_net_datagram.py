import socket
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c
from picoscript_il import lower_to_bytecode_safe
from picoscript_vm import HostApi, PicoVM, SocketNetworkProvider


def test_datagram_provider_round_trip_and_peer_endpoint():
    provider = SocketNetworkProvider()
    host = HostApi(network_provider=provider)
    source = r'''
int h = Net.DatagramBind(0);
int payload = Net.DatagramRecv(h, 64);
int peer = Net.DatagramPeer(h);
int ok = Net.DatagramSetPeer(h, peer);
int sent = Net.DatagramSend(h, payload);
Io.WriteByte(Span.Len(peer));
Io.WriteByte(sent);
Net.DatagramClose(h);
'''
    result = {}

    def run_server():
        result["vm"] = PicoVM(host=host).run(
            lower_to_bytecode_safe(compile_c(source))
        )

    thread = threading.Thread(target=run_server)
    thread.start()
    deadline = time.time() + 5
    while not provider._sockets and time.time() < deadline:
        time.sleep(0.01)
    assert provider._sockets
    handle = min(provider._sockets)
    port = provider.local_port(handle)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.bind(("127.0.0.1", 0))
        client.sendto(b"ping", ("127.0.0.1", port))
        client.settimeout(5)
        data, _ = client.recvfrom(64)
    thread.join(timeout=5)
    provider.close_all()
    assert not thread.is_alive()
    assert data == b"ping"
    assert b"".join(result["vm"].output) == bytes([6, 4])
