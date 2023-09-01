from nllink import export
import asyncio

class Timer:
    "A class that implements a timer."

    def __init__(self, seconds, invited_to):
        """Initialize the timer with a number of minutes."""
        self.minutes = seconds 
        self.channel = invited_to[0]
    
    async def send_time(self, send=None):
        "Send the time every configured minutes."
        while True:
            await asyncio.sleep(self.minutes)
            if send:
                await send("Time has passed: {} minutes".format(self.minutes), channel=self.channel)

if __name__ == '__main__':
    export(Timer, channel="test_timer")