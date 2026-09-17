"""Wire-level tests: real pyserial/minimalmodbus code talking to emulators over a pty.

The emulators follow the instrument manuals byte for byte:
* EG&G 5302 RS-232: echo, CR LF response terminator, '*' / '?' prompt (manual 8.4-8.7).
* Omega CND3 Modbus ASCII / RTU, including the manual's example request frames.
"""

import os
import select
import threading

import pytest

# The skip has to happen while importing, not as a mark: tty (and termios under it)
# do not exist on Windows, so a plain "import tty" fails during collection before
# any skipif is consulted.
tty = pytest.importorskip("tty", reason="the pty emulators need a POSIX terminal")

from ferro.instruments.cnd3 import CND3, PIDSensorError, autodetect  # noqa: E402
from ferro.instruments.lockin5302 import Lockin5302  # noqa: E402
from ferro.transports import SerialTransport  # noqa: E402


class PtyDevice:
    def __init__(self, handler):
        self.master, slave = os.openpty()
        tty.setraw(self.master)
        tty.setraw(slave)
        self.port = os.ttyname(slave)
        self._slave = slave
        self.handler = handler
        self.received = bytearray()
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        buf = bytearray()
        while not self._stop:
            # select with a timeout so close() never races a blocking read (hangs on macOS)
            if not select.select([self.master], [], [], 0.05)[0]:
                continue
            try:
                chunk = os.read(self.master, 256)
            except OSError:
                return
            self.received += chunk
            buf += chunk
            buf = self.handler(self, buf)

    def send(self, data: bytes):
        os.write(self.master, data)

    def close(self):
        self._stop = True
        self._thread.join(2)
        for fd in (self.master, self._slave):
            try:
                os.close(fd)
            except OSError:
                pass


# --- EG&G 5302 over RS-232 ---------------------------------------------------------------
def lockin_handler(dev, buf):
    state = dev.__dict__.setdefault("state", {"SEN": 12, "EX": 0})
    while b"\r" in buf:
        line, _, rest = bytes(buf).partition(b"\r")
        buf = bytearray(rest.lstrip(b"\n"))
        dev.send(line + b"\r\n")  # echo is ON by default
        parts = line.decode().upper().split()
        cmd, args = (parts[0], parts[1:]) if parts else ("", [])
        prompt = b"*"
        if cmd == "ID":
            dev.send(b"5302\r\n")
        elif cmd in ("SEN", "EX"):
            if args:
                state[cmd] = int(args[0])
            else:
                dev.send(f"{state[cmd]}\r\n".encode())
        elif cmd == "XY":
            dev.send(b"5000,-2500\r\n")
        else:
            prompt = b"?"
        dev.send(prompt)
    return buf


def test_lockin_rs232_echo_and_prompt():
    dev = PtyDevice(lockin_handler)
    try:
        li = Lockin5302(SerialTransport(dev.port, timeout_s=2.0))
        assert li.check() == "5302"
        li.set_sensitivity(15)  # 10 mV; echoed "SEN 15" must not be mistaken for data
        r = li.read()
        assert (r.x_counts, r.y_counts, r.sen_index) == (5000, -2500, 15)
        assert r.x_v == pytest.approx(5e-3) and r.y_v == pytest.approx(-2.5e-3)
        assert b"SEN 15\r" in dev.received and b"XY\r" in dev.received
        li.close()
    finally:
        dev.close()


# --- Omega CND3 Modbus -------------------------------------------------------------------
REGISTERS = {0x1000: 0x05DC, 0x1001: 0x0640, 0x102F: 0x0100}


def lrc(data: bytes) -> int:
    return (-sum(data)) & 0xFF


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def ascii_handler(dev, buf):
    while b"\r\n" in buf:
        frame, _, rest = bytes(buf).partition(b"\r\n")
        buf = bytearray(rest)
        body = bytes.fromhex(frame[1:].decode())
        assert frame[:1] == b":" and lrc(body[:-1]) == body[-1], frame
        addr, fc, reg, count = body[0], body[1], int.from_bytes(body[2:4], "big"), int.from_bytes(body[4:6], "big")
        if addr != dev.__dict__.get("address", 1):
            continue
        data = b"".join(REGISTERS.get(reg + i, 0).to_bytes(2, "big") for i in range(count))
        reply = bytes([addr, fc, len(data)]) + data
        dev.send(b":" + (reply + bytes([lrc(reply)])).hex().upper().encode() + b"\r\n")
    return buf


def rtu_handler(dev, buf):
    while len(buf) >= 8:
        frame, buf = bytes(buf[:8]), bytearray(buf[8:])
        assert crc16(frame[:6]) == int.from_bytes(frame[6:], "little"), frame.hex()
        addr, fc, reg, count = frame[0], frame[1], int.from_bytes(frame[2:4], "big"), int.from_bytes(frame[4:6], "big")
        data = b"".join(REGISTERS.get(reg + i, 0).to_bytes(2, "big") for i in range(count))
        reply = bytes([addr, fc, len(data)]) + data
        dev.send(reply + crc16(reply).to_bytes(2, "little"))
    return buf


def test_cnd3_modbus_ascii_matches_manual_frame():
    dev = PtyDevice(ascii_handler)
    try:
        pid = CND3(dev.port)  # factory defaults: ASCII, addr 1, 9600 7E1
        r = pid.read()
        assert (r.pv_c, r.sv_c) == (150.0, 160.0)
        # Manual example: read 2 words from 1000H at address 1, LRC = EAH
        assert dev.received.startswith(b":010310000002EA\r\n")
        assert pid.version() == "V1.00"
        pid.close()
    finally:
        dev.close()


def test_cnd3_modbus_rtu_matches_manual_frame():
    dev = PtyDevice(rtu_handler)
    try:
        pid = CND3(dev.port, mode="rtu", bytesize=8, parity="N", stopbits=1)
        assert pid.read().pv_c == 150.0
        # Manual example RTU request: 01 03 10 00 00 02 C0 CB
        assert bytes(dev.received[:8]) == bytes.fromhex("010310000002C0CB")
        pid.close()
    finally:
        dev.close()


def test_cnd3_sensor_fault_is_reported():
    REGISTERS[0x1000] = 0x8003
    dev = PtyDevice(ascii_handler)
    try:
        pid = CND3(dev.port)
        with pytest.raises(PIDSensorError, match="not connected"):
            pid.read()
        pid.close()
    finally:
        REGISTERS[0x1000] = 0x05DC
        dev.close()


def test_autodetect_finds_factory_default():
    dev = PtyDevice(ascii_handler)
    try:
        found = autodetect(dev.port)
        assert found and found["mode"] == "ascii" and found["address"] == 1
    finally:
        dev.close()
