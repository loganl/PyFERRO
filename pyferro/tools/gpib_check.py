"""Ask the lock-in's GPIB link a few questions and print a verdict you can read aloud.

    pixi run gpib                       # GPIB0::12::INSTR
    pixi run gpib GPIB0::8::INSTR       # somewhere else

Nothing here is clever: it is the same pyvisa the program uses, doing the smallest
steps in order, so a failure lands on one of them instead of on "VI_ERROR_TMO".
The serial poll is the useful one. It is answered by the instrument's GPIB
interface chip rather than by its command processor, so it separates "nothing is
on the bus" from "the interface is alive but commands are not getting through".
"""

import sys

RESOURCE = sys.argv[1] if len(sys.argv) > 1 else "GPIB0::12::INSTR"
TERMINATORS = [("\r", "\r"), ("\r", "\r\n"), ("\r\n", "\r\n"), ("\r\n", "\r"),
               ("\r", None), ("\r\n", None), ("\n", "\n"), ("\n", None)]
NAMES = {"\r": "CR", "\n": "LF", "\r\n": "CRLF", None: "EOI"}
STATUS_BITS = [(1, "command complete"), (2, "invalid command"), (4, "parameter error"),
               (8, "reference unlock"), (16, "overload"), (32, "ext trig"),
               (64, "SRQ"), (128, "data available")]


def main() -> int:
    try:
        import pyvisa
    except ImportError:
        print("pyvisa is not installed - run this with 'pixi run gpib'")
        return 2

    rm = pyvisa.ResourceManager()
    print(f"VISA library : {rm.visalib}")
    if "py" in str(rm.visalib).lower().split()[-1]:
        print("  ! this is pyvisa-py, not NI-VISA. It cannot drive an NI GPIB adapter.")
        print("    Install NI-VISA (64-bit) alongside NI-488.2 and try again.")
    try:
        found = list(rm.list_resources())
    except Exception as exc:
        found = []
        print(f"list_resources failed: {exc}")
    print(f"resources    : {', '.join(found) if found else '(none)'}")
    print(f"target       : {RESOURCE}")
    if found and RESOURCE not in found:
        print("  ! the target is not in the list above - check the address in NI MAX")

    try:
        inst = rm.open_resource(RESOURCE)
    except Exception as exc:
        print(f"\nopen         : FAILED - {exc}")
        print("\nVERDICT: cannot open the resource at all. Wrong address, or NI-488.2 "
              "cannot see the adapter.")
        return 1
    print("open         : ok")

    stb = None
    try:
        inst.timeout = 2000
        stb = int(inst.read_stb())
        bits = ", ".join(text for bit, text in STATUS_BITS if stb & bit) or "no bits set"
        print(f"serial poll  : 0x{stb:02X} ({bits})")
    except Exception as exc:
        print(f"serial poll  : FAILED - {type(exc).__name__}")

    answered = None
    for write_t, read_t in TERMINATORS:
        label = f"write {NAMES[write_t]}, read {NAMES[read_t]}"
        try:
            inst.write_termination, inst.read_termination = write_t, read_t
            inst.timeout = 1500
            reply = inst.query("ID").strip()
            print(f"ID / {label:<22}: {reply!r}")
            if "5302" in reply:
                answered = label
                break
        except Exception:
            print(f"ID / {label:<22}: timeout")
    inst.close()

    print()
    if answered:
        print(f"VERDICT: the lock-in answers. Use {answered}.")
        return 0
    if stb is not None:
        print("VERDICT: the GPIB interface is alive (the serial poll worked) but the "
              "instrument never answers ID. Power-cycle the 5302 - its command "
              "processor is hung. If that does not help, check the ADDRESS on its "
              "COMM-I/O screen still reads the address above.")
        return 1
    print("VERDICT: nothing is answering on the bus. Reseat both ends of the GPIB "
          "cable and tighten the screws, make sure every instrument in the chain is "
          "powered on, and close any other program holding the board.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
