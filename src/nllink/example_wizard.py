from nllink import export
from nllink.wizardable import Wizardable

if __name__ == '__main__':
    export(Wizardable.concat_strings, channel_suffix='_concat')
    export(Wizardable.add_numbers, channel_suffix='_add')
    # all other methods will be exported to the main channel the bot is invited to
    export(Wizardable, channel="test_wizardable")