"""Text-command transports for the lock-in and multimeter.

Two implementations share one tiny interface (``write``, ``query``, ``close``):

* :class:`VisaTransport` - GPIB (or anything else VISA can open). This is how the
  instruments were wired for the LabVIEW routine (lock-in GPIB0::12, DMM GPIB0::24).
* :class:`SerialTransport` - a plain RS-232 port, handling the EG&G 5302's
  character echo and ``*``/``?`` prompt (5302 manual, sections 8.4 and 8.7).

Every call is serialised with a lock so a GUI "Test" button can never interleave
with the acquisition loop on the same port.
"""

from __future__ import annotations

import threading
import time


class TransportError(RuntimeError):
    """Raised when an instrument cannot be reached or answers garbage."""


class Transport:
    name = "transport"

    def write(self, cmd: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def query(self, cmd: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        pass


# GPIB terminators, most likely first. The 5302 offers CR or CR LF, set from the
# front panel (SETUP MENU -> COMM-I/O -> GPIB) or with the AT and GP commands, and
# the input and output terminators are selected independently - so all four pairings
# occur (manual section 8.5). CR both ways is the GPIB default. read_termination
# None reads until EOI, for an instrument that is not terminating at all. Guessing
# wrong means the command is never recognised and the read times out with
# VI_ERROR_TMO, which looks exactly like a dead instrument.
TERMINATIONS = [
    ("\r", "\r"),
    ("\r", "\r\n"),
    ("\r\n", "\r\n"),
    ("\r\n", "\r"),
    ("\r", None),
    ("\r\n", None),
    ("\n", "\n"),
    ("\n", None),
]
# 5302 status byte (manual section 8.7). A serial poll works even when a command
# has been rejected, so it turns a bare VI_ERROR_TMO into the actual reason.
STATUS_BITS = [
    (1 << 1, "invalid command"),
    (1 << 2, "command parameter error"),
    (1 << 3, "reference unlock"),
    (1 << 4, "overload"),
]


def drain_replies(read, limit: int = 2) -> int:
    """Read and discard replies left queued, until there are none or ``limit`` is hit.

    A query that times out can still have its reply delivered afterwards, and then
    the *next* command reads it instead of its own - a good SEN or XTC answer comes
    back as the previous response, which is how a value like 5302 turns up where
    0..21 was expected. Called only after a failure, so the working path is untouched.
    """
    drained = 0
    while drained < limit:
        try:
            read()
        except Exception:
            break
        drained += 1
    return drained


def describe_status(stb: int) -> str:
    faults = [text for bit, text in STATUS_BITS if stb & bit]
    if not faults:
        return ""
    return " - the instrument reports " + ", ".join(faults)


_TERM_NAMES = {"\r": "CR", "\n": "LF", "\r\n": "CRLF", None: "EOI"}


def termination_label(write_termination: str, read_termination: str | None) -> str:
    return f"write {_TERM_NAMES[write_termination]}, read {_TERM_NAMES[read_termination]}"


def probe_terminations(open_one, verify, name: str = "instrument", terminations=None,
                       should_stop=None):
    """Open ``name`` with each terminator pair until ``verify`` accepts the result.

    ``open_one(write_termination, read_termination)`` returns a transport and
    ``verify(transport)`` raises if the instrument does not answer properly.
    Returns ``(transport, label)``; the winning pair is left on the transport as
    ``terminators`` so a caller can try it first next time. Raises TransportError
    naming everything tried, so a genuine wiring fault does not read as a
    terminator problem.

    ``should_stop()`` is checked before each attempt. A silent instrument costs a
    timeout per pair, which is far longer than anyone expects to wait for a Stop
    button, so this has to be interruptible.
    """
    attempts = []
    for write_t, read_t in terminations or TERMINATIONS:
        if should_stop is not None and should_stop():
            raise TransportError(f"{name}: gave up looking for a terminator (stopping)")
        label = termination_label(write_t, read_t)
        transport = None
        try:
            transport = open_one(write_t, read_t)
            verify(transport)
            transport.terminators = (write_t, read_t)
            transport.detected = label
            return transport, label
        except Exception as exc:
            attempts.append(f"  {label}: {exc}")
            if transport is not None:
                transport.close()
    raise TransportError(
        f"{name} did not answer with any terminator. Check the address, the cable, and that "
        f"the instrument is not switched to RS-232.\n" + "\n".join(attempts)
    )


def list_visa_resources() -> list[str]:
    """Best-effort list of VISA resources; never raises."""
    for backend in ("", "@py"):
        try:
            import pyvisa

            rm = pyvisa.ResourceManager(backend)
            found = list(rm.list_resources())
            if found:
                return found
        except Exception:
            continue
    return []


class VisaTransport(Transport):
    """GPIB/VISA transport.

    ``backend`` "" uses NI-VISA (needed for NI GPIB adapters on the lab PC) and
    falls back to the pure-Python ``@py`` backend when NI-VISA is not installed.
    """

    def __init__(
        self,
        resource: str,
        timeout_s: float = 2.0,
        write_termination: str = "\r",
        read_termination: str = "\r",
        backend: str = "",
        settle_s: float = 0.15,
        gap_s: float = 0.0,
    ) -> None:
        import pyvisa

        self.name = resource
        self._gap = gap_s
        self._lock = threading.Lock()
        errors = []
        backends = [backend] if backend else ["", "@py"]
        self._inst = None
        for be in backends:
            try:
                rm = pyvisa.ResourceManager(be)
                inst = rm.open_resource(resource)
                inst.timeout = int(timeout_s * 1000)
                inst.write_termination = write_termination
                inst.read_termination = read_termination
                self._inst = inst
                break
            except Exception as exc:  # try the next backend
                errors.append(f"{be or 'NI-VISA'}: {exc}")
        if self._inst is None:
            raise TransportError(f"Could not open {resource} ({'; '.join(errors)})")
        try:
            self._inst.clear()
        except Exception:
            pass
        # Device Clear resets the instrument's communications processor. The 5302 can
        # swallow a command sent immediately afterwards, which then looks like a dead
        # instrument rather than a lost byte.
        time.sleep(settle_s)
        # Whatever a previous program left unread would be handed to us as our own
        # first reply, one command out of step from the start. On this rig ibic found
        # two ID responses stacked up from earlier attempts.
        self._recover(limit=4)

    def _pause(self) -> None:
        """Let the instrument finish before the next command.

        The 5302 loses a command sent while it is still dealing with the previous
        one, which shows up as a timeout on a perfectly valid command a few
        exchanges into a run. Serial-polling for command complete, as the manual
        describes, upset it further - a fixed gap is enough and touches nothing.
        """
        if self._gap:
            time.sleep(self._gap)

    def _recover(self, limit: int = 2) -> None:
        """Clear replies that may still be queued, so the next read is this one's."""
        saved = None
        try:
            saved = self._inst.timeout
            self._inst.timeout = 200
            drain_replies(self._inst.read, limit)
        except Exception:
            pass
        finally:
            if saved is not None:
                try:
                    self._inst.timeout = saved
                except Exception:
                    pass

    def write(self, cmd: str) -> None:
        with self._lock:
            try:
                self._inst.write(cmd)
                self._pause()
            except Exception as exc:
                self._recover()
                raise TransportError(f"{self.name}: write {cmd!r} failed: {exc}") from exc

    def _status_hint(self) -> str:
        """Serial-poll the instrument after a failure; silent if that fails too."""
        try:
            return describe_status(int(self._inst.read_stb()))
        except Exception:
            return ""

    def query(self, cmd: str) -> str:
        with self._lock:
            try:
                reply = self._inst.query(cmd).strip()
                self._pause()
                return reply
            except Exception as exc:
                hint = self._status_hint()
                self._recover()
                raise TransportError(f"{self.name}: query {cmd!r} failed: {exc}{hint}") from exc

    def set_timeout(self, timeout_s: float) -> None:
        try:
            self._inst.timeout = int(timeout_s * 1000)
        except Exception:
            pass

    def read(self) -> str:
        """Read one more response line (e.g. the second value of a compound reply)."""
        with self._lock:
            try:
                return self._inst.read().strip()
            except Exception as exc:
                raise TransportError(f"{self.name}: read failed: {exc}") from exc

    def close(self) -> None:
        try:
            self._inst.close()
        except Exception:
            pass


class SerialTransport(Transport):
    """RS-232 transport for EG&G-style instruments.

    After each command the 5302 sends: optional echo, response line(s) ending in
    CR LF, then a prompt character (``*`` = OK, ``?`` = error/overload/unlock).
    We read until the prompt so responses can never drift out of step.
    """

    PROMPTS = (b"*", b"?")

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        bytesize: int = 7,
        parity: str = "E",
        stopbits: int = 1,
        timeout_s: float = 2.0,
        terminator: str = "\r",
        serial_factory=None,
    ) -> None:
        self.name = port
        self._lock = threading.Lock()
        self._timeout = timeout_s
        self._term = terminator.encode()
        self.last_prompt = b"*"
        try:
            if serial_factory is None:
                import serial

                serial_factory = serial.Serial
            self._ser = serial_factory(
                port=port,
                baudrate=baudrate,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                timeout=0.05,
            )
        except Exception as exc:
            raise TransportError(f"Could not open serial port {port}: {exc}") from exc

    def _exchange(self, cmd: str) -> str:
        self._ser.reset_input_buffer()
        self._ser.write(cmd.encode("ascii") + self._term)
        buf = bytearray()
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            chunk = self._ser.read(64)
            if chunk:
                buf += chunk
                tail = bytes(buf).rstrip(b"\r\n ")
                if tail.endswith(self.PROMPTS) and (len(tail) == 1 or tail[-2:-1] in (b"\n", b"\r") or tail[:-1].strip() == cmd.encode()):
                    self.last_prompt = tail[-1:]
                    body = tail[:-1]
                    break
        else:
            raise TransportError(f"{self.name}: no prompt after {cmd!r} (got {bytes(buf)!r})")
        text = body.decode("ascii", "replace").strip()
        if text.upper().startswith(cmd.upper()):  # strip RS-232 echo
            text = text[len(cmd):]
        return text.strip()

    def write(self, cmd: str) -> None:
        with self._lock:
            self._exchange(cmd)

    def query(self, cmd: str) -> str:
        with self._lock:
            return self._exchange(cmd)

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass
