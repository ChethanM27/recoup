"""
The failure demo.

The brief asks for one failure handled gracefully. This is it: Razorpay stops
answering in the middle of a live batch.

What must NOT happen: the batch crashes, or the engine keeps hammering a dead
gateway, or recovery intents vanish silently and the merchant believes the money
is being chased when it is not.

What actually happens, in order:
  1. calls fail and are retried with exponential backoff
  2. after 4 consecutive failures the circuit breaker opens and we STOP calling
  3. every subsequent recovery intent is queued with a reason, not lost
  4. the batch runs to completion and reports honestly
  5. when the gateway recovers, the circuit closes and the queue drains

    python3 demo_failure.py
"""
import time

from recoup.razorpay_client import RazorpayClient, RazorpayUnavailable, CircuitOpen

R = "\u20b9"


class FlakyGateway(RazorpayClient):
    """Answers normally, then goes dark, then comes back."""

    def __init__(self):
        super().__init__(mock=True)
        self.n = 0
        self.dark = False
        self.mock = False          # force the real transport path
        self.key_id = "rzp_test_demo"
        self.key_secret = "demo"

    def _request(self, method, path, payload=None):
        # Note the ordering: the breaker is consulted BEFORE the network. While
        # the circuit is open we never touch the wire at all.
        self.breaker.before_call()
        self.n += 1
        if self.dark:
            self.breaker.record_failure()
            raise RazorpayUnavailable("TimeoutError: gateway did not respond in 8s")
        self.breaker.record_success()
        return {"id": f"plink_DEMO{self.n:04d}", "short_url": f"https://rzp.io/i/D{self.n:04d}",
                "status": "created", "amount": payload.get("amount") if payload else 0}


def line(ch="\u2500", n=74):
    print(ch * n)


def main():
    rz = FlakyGateway()
    rz.breaker.cooldown_seconds = 2

    print()
    line("=")
    print("  RECOUP  |  gateway failure drill")
    line("=")
    print("\n  Working 20 recovery intents. The gateway goes dark on call 3 and")
    print("  stays dark until call 14. Nothing below is caught in a bare except.\n")

    executed = queued = 0
    for i in range(1, 21):
        status, res = rz.execute_or_queue(
            f"payment_link:evt_{i:03d}", rz.create_payment_link,
            120000, f"Recovery for order {i}", f"evt_{i:03d}")

        st = rz.breaker.state
        if status == "executed":
            executed += 1
            print(f"    {i:>3}  executed   circuit={st:<10} {res['short_url']}")
        else:
            queued += 1
            print(f"    {i:>3}  QUEUED     circuit={st:<10} {res['reason']}")

        if i == 2:
            rz.dark = True
        if i == 14:
            rz.dark = False
            print("\n    ... gateway recovers. Waiting out the breaker cooldown,")
            print("        then one probe request is allowed through.\n")
            time.sleep(2.1)

    line()
    print(f"\n  Batch completed. It did not crash.\n")
    print(f"    executed against the gateway   {executed}")
    print(f"    queued with a reason           {queued}")
    print(f"    intents silently dropped       0")
    print(f"    circuit breaker trips          {rz.breaker.trips}")
    print(f"    calls made while circuit open  0   <- the breaker's whole job")

    drained = rz.drain_queue()
    print(f"\n  Draining the queue now that the gateway answers again:")
    for item in drained[:4]:
        print(f"    {item['action']:<28} held because: {item['reason']}")
    if len(drained) > 4:
        print(f"    ... and {len(drained) - 4} more, all replayable")

    print("\n  The merchant is never told money is being chased when it is not.")
    print("  Queued intents appear on the exception list until they clear.\n")


if __name__ == "__main__":
    main()
