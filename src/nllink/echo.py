"""Example class for testing purposes."""

from nllink import UsageError

class Echo:
    """Will return the modified input string. Each method provides different modifications."""

    def same_string(self, string):
        """Returns the same string with no modifications."""
        return string
    
    def upper_string(self, string):
        """Returns the string in uppercase."""
        return string.upper()
    
    def lower_string(self, string):
        """Returns the string in lowercase."""
        return string.lower()
    
    def reverse_string(self, string):
        """Returns the string in reverse."""
        return string[::-1]

    # more complex test with multiple inputs
    def concat_strings(self, string1, string2):
        """Returns the concatenation of two strings."""
        return string1 + string2
    
    # with numbers
    def add_numbers(self, number1, number2):
        """Returns the sum of two numbers."""
        return number1 + number2
    
    # raise an exception
    def raise_exception(self, string):
        """Raises an exception with explanation."""
        raise UsageError("Do not use this method!")
    
    # use nickname as kwarg to say hello with a string
    def say_hello(self, nickname):
        """Returns a greeting with the nickname of bot's user."""
        return f"Hello, {nickname}!"
