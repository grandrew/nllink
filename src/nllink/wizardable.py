from nllink.echo import Echo

class Wizardable(Echo):
    "A demonstration of class with wizard-based configuration."

    def __init__(self, username, password, url):
        """Initialize the wizardable with a username, password, and url.

        Arguments:
        username (str) -- the username to use for authentication
        password (str) -- the password to use for authentication
        url (str) -- the url to connect to
        """

        self.username = username
        self.password = password
        self.url = url