"""NLLink - Natural Language communication link between Magic-R magic creatures talking on IRC channels and Python language."""


import sys

if sys.version_info[:2] >= (3, 8):
    # TODO: Import directly (no need for conditional) when `python_requires = >= 3.8`
    from importlib.metadata import PackageNotFoundError, version  # pragma: no cover
else:
    from importlib_metadata import PackageNotFoundError, version  # pragma: no cover

try:
    # Change here if project is renamed and does not equal the package name
    dist_name = __name__
    __version__ = version(dist_name)
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"
finally:
    del version, PackageNotFoundError

import time
import os
import threading
import pickle
import re
import glob
import inspect
import base64
import json
from pathlib import Path
import asyncio
from asyncio import gather
import asyncio
from collections import defaultdict
from logzero import logger as log
sys.modules["asyncio.coroutine"] = asyncio
import pydle


NAME_DIGITS = 5
MAX_DEPTH = 5
DEFAULT_CHANNEL = "#export_bots"


AUTO_PARAMS = ("invited_to", "nickname", "realname", "assigned_user", "target", "source", "send")


# monkeypatch pydle
def __handle_forever(self):
    """ Main loop of the pool: handle clients forever, until the event loop is stopped. """
    # container for all the client connection coros
    connection_list = []
    for client in self.clients:
        args, kwargs = self.connect_args[client]
        connection_list.append(client.connect(*args, **kwargs))
    # single future for executing the connections
    asyncio.set_event_loop(self.eventloop)
    connections = gather(*connection_list)

    # run the connections
    self.eventloop.run_until_complete(connections)

    # run the clients
    self.eventloop.run_forever()
pydle.client.ClientPool.handle_forever = __handle_forever


async def __connect(self):
    """ Connect to target. """
    self.tls_context = None

    if self.tls:
        self.tls_context = self.create_tls_context()
    
    asyncio.set_event_loop(self.eventloop)

    (self.reader, self.writer) = await asyncio.open_connection(
        host=self.hostname,
        port=self.port,
        local_addr=self.source_address,
        ssl=self.tls_context
    )
pydle.connection.Connection.connect = __connect


class UsageError(Exception):
    """Raised when function call fails due to incorrect usage."""
    pass


def zoo_name_from_nameid(nameid):
    """Return if it is elf, pixie or reindeer by removing numeric part from name"""
    # use filter for non-numerics
    return "".join(filter(str.isalpha, nameid))


class IRCExportBot(pydle.Client):
    """IRC bot that exports objects to IRC as Magic-R natural language format."""

    # TODO: don't forget to add new instances to the instances list
    def __init__(self, nickname, chan_free, obj, instantiator=None, *args, **kwargs):
        if isinstance(obj, type):
            self.obj = None
            self.class_ = obj
        else:
            self.obj = obj
            self.class_ = type(obj)
        self.instantiator = instantiator
        self.save_path = None
        self.pool = None
        self.chan_free = chan_free
        self.nickname = nickname
        self.joined_channels = []
        self.invited_channels = []
        log.info("Creating bot %s", self.nickname)
        self.assigned_user = None
        self.join_updated = False
        self.messages = []
        self.waiting = False
        self.last_source = None
        self.last_target = None
        self.log_lines = []
        self.last_sent_to = None
        self.last_sent_time = time.time()
        self.last_sent_depth = 0
        self.delayed_process_message_task = None
        self.post_init()
        super().__init__(nickname, *args, **kwargs)
    
    def post_init(self):
        """Called after initialization."""
        self.real_name = self.nickname
        self.real_name_temp = self.nickname
        self.tasks = []

    async def on_connect(self):
        self.RECONNECT_MAX_ATTEMPTS = None
        self.RECONNECT_DELAYED = False
        log.debug(f"{self.nickname} Current channels: {self.joined_channels}")
        if len(self.joined_channels) == 0 and self.assigned_user is None:
            log.debug(f"{self.nickname} Joining free channel {self.chan_free}")
            await self.join(self.chan_free)
        else:
            for channel in self.joined_channels:
                await self.join(channel)
            if self.obj is not None and len(self.joined_channels) > 0:
                # restore any infinite loops
                await self.join_supplemental_channels()
    
    def __getstate__(self):
        return [None, self.assigned_user, None, None, self.joined_channels, self.nickname, self.log_lines, self.realname, self.chan_free, self.obj, self.class_, self.invited_channels]
    
    def __setstate__(self, state):
        self.pool = None
        self.instantiator = None  # Pre-loaded bots don't need instantiator as they can't replace themselves
        self.save_path = None
        self.last_sent_to = None
        self.delayed_process_message_task = None
        self.last_sent_time = time.time()
        self.last_sent_depth = 0
        self.assigned_user = state[1]
        self.joined_channels = list(set(state[4]))
        self.nickname = state[5]
        self.join_updated = False
        self.messages = []
        self.waiting = False
        self.last_source = None
        self.last_target = None
        self.log_lines = state[6]
        self.realname = state[7]
        self.chan_free = state[8]
        self.obj = state[9]
        self.class_ = state[10]
        try:
            self.invited_channels = state[11]
        except IndexError:
            pass
        self.post_init()
        super().__init__(self.nickname, realname=self.realname)
    
    def replace_free_self_if_needed(self):
        if self.pool is None: return
        new_id = self.pool.next_id
        self.pool.next_id += 1
        new_nickname = zoo_name_from_nameid(self.nickname) + f"{new_id}".rjust(NAME_DIGITS, "0")
        obj_cls = self.class_  
        sig = inspect.signature(obj_cls.__init__)
        new_elf = IRCExportBot(new_nickname, realname=self.real_name_temp, chan_free=self.chan_free, obj=self.obj, instantiator=self.instantiator)
        new_elf.class_ = self.class_  # FIXME: interface is not preserved
        new_elf.pool = self.pool
        self.pool.bot_instances.append(new_elf)
        event_loop = self.pool.eventloop
        self.pool.connect(new_elf, self.connection.hostname, port=self.connection.port, tls=False, tls_verify=False)
        event_loop.create_task(new_elf.connect(self.connection.hostname, port=self.connection.port, tls=False, tls_verify=False))
    
    def fill_kwargs(self, parameters, kwargs, reply_target, source, invited_to):
        if "nickname" in parameters:
            kwargs["nickname"] = self.nickname
        if "assigned_user" in parameters:
            kwargs["assigned_user"] = self.assigned_user
        if "target" in parameters:
            kwargs["target"] = reply_target
        if "source" in parameters:
            kwargs["source"] = source
        if "invited_to" in parameters:
            kwargs["invited_to"] = self.joined_channels + [invited_to]

    async def try_instantiate(self, reply_target, source, invited_to):
        obj_cls = self.class_  
        sig = inspect.signature(obj_cls.__init__)
        kwargs = {}
        if "nickname" in sig.parameters:
            kwargs["nickname"] = self.nickname
        if "assigned_user" in sig.parameters:
            kwargs["assigned_user"] = self.assigned_user
        if "target" in sig.parameters:
            kwargs["target"] = reply_target
        if "source" in sig.parameters:
            kwargs["source"] = source
        if "invited_to" in sig.parameters:
            kwargs["invited_to"] = self.joined_channels + [invited_to]
        if self.instantiator is not None:
            try:
                self.obj = self.instantiator()
                log.debug(f"Instantiated {self.nickname} with instantiator function.")
                await self.join_supplemental_channels()
                return True
            except:
                log.error("Instantiator failed to instantiate object with provided instantiator function.")
                return False
        elif len(set(sig.parameters) - {'args', 'kwargs', 'self'}) == 0:
            try:
                self.obj = obj_cls(**kwargs)
                log.debug(f"Instantiated {self.nickname} with {kwargs}")
                await self.join_supplemental_channels()
                return True
            except:
                log.error("Instantiator failed to instantiate object with no arguments.")
                return False
        return False  # can't instantiate without arguments
    
    async def join_supplemental_channels(self):
        # for every method in self.obj,
        for method_name in dir(self.obj):
            method = getattr(self.obj, method_name)
            if not callable(method): continue
            if method_name.startswith("_"): continue
            log.debug(f"Exporting method {method_name}")
            invited_to = self.joined_channels.copy()
            if method_name in self.class_._export_metadata:
                if "channel" in self.class_._export_metadata[method_name]:
                    for chan in self.class_._export_metadata[method_name]["channel"]:
                        if chan not in self.joined_channels:
                            log.info(f"Joining supplemental channel {chan}")
                            self.joined_channels.append(chan)
                            await self.join(chan)
                        invited_to.append(chan)
                if "channel_suffix" in self.class_._export_metadata[method_name]:
                    for chan_sfx in self.class_._export_metadata[method_name]["channel_suffix"]:
                        chan = self.invited_channels[0] + chan_sfx
                        if chan not in self.joined_channels:
                            log.info(f"Joining supplemental channel {chan}")
                            self.joined_channels.append(chan)
                            await self.join(chan)
                        invited_to.append(chan)
            if inspect.iscoroutinefunction(method):
                if "while True:" in inspect.getsource(method):
                    log.debug(f"Method {method_name} is infinite loop - starting task")
                    sig = inspect.signature(method)
                    kwargs = {}
                    self.fill_kwargs(sig.parameters, kwargs, self.nickname, self.nickname, invited_to)
                    if "send" in sig.parameters:
                        local_method_name = method_name
                        def sendfunc(message, channel=None):
                            if channel is None: 
                                # check if we have a dedicated metadata channel
                                print(local_method_name, self.class_._export_metadata)
                                if local_method_name in self.class_._export_metadata and "channel" in self.class_._export_metadata[local_method_name]:
                                    channel = self.class_._export_metadata[local_method_name]["channel"][0]
                                    # TODO: support multiple channels
                                # now check channel suffix
                                if local_method_name in self.class_._export_metadata and "channel_suffix" in self.class_._export_metadata[local_method_name]:
                                    if channel is None: channel = self.invited_channels[0]
                                    channel = channel + self.class_._export_metadata[local_method_name]["channel_suffix"][0] 
                                if channel is None:
                                    channel = self.invited_channels[0]
                            return self.message(channel, message)
                        kwargs["send"] = sendfunc
                    event_loop = self.pool.eventloop
                    self.tasks.append(event_loop.create_task(method(**kwargs)))

    def clean_params(self, params):
        if "self" in params: params.remove("self")
        if "args" in params: params.remove("args")
        if "kwargs" in params: params.remove("kwargs")

        # also remove any parameters that can be inferred from the bot's attributes or call context
        if "invited_to" in params: params.remove("invited_to")
        if "nickname" in params: params.remove("nickname")
        if "realname" in params: params.remove("realname")
        if "assigned_user" in params: params.remove("assigned_user")
        if "target" in params: params.remove("target")
        if "source" in params: params.remove("source")
        if "send" in params: params.remove("send")
        return params

    def instantiation_instructions(self):
        """Return instructions for instantiating this bot's class."""
        PREFACE = "This bot uses an object-oriented interface with a class that must be instantiated with additional arguments before use. See below for instructions on how to do so."
        obj_cls = self.class_
        sig = inspect.signature(obj_cls.__init__)
        params = list(sig.parameters.keys())
        # we assume there are additional arguments as otherwise we would have been instantiated already
        params = self.clean_params(params)
        assert len(params) > 0
        params_str = ", ".join([f"<{p}>" for p in params])
        init_docstring = inspect.getdoc(obj_cls.__init__)
        class_docstring = inspect.getdoc(obj_cls)
        if init_docstring is None: init_docstring = "Should be self-explanatory."
        if class_docstring is None: class_docstring = "Should be self-explanatory."
        class_name = obj_cls.__name__
        all_methods_summaries = []
        for method_name in dir(self.class_):
            if method_name.startswith("_"):
                continue
            method = getattr(self.class_, method_name)
            if callable(method):
                method_signature = inspect.signature(method)
                params = list(method_signature.parameters.keys())
                params = self.clean_params(params)
                params_string = ", ".join([f"<{p}>" for p in params])
                chans_avail_list = ", ".join(self.func_chan_avail(method_name))
                method_docstring = method.__doc__ or "Should be self-explanatory"
                all_methods_summaries.append(f"Method '{method_name}({params_string}) [available after instantiation on channel(s): {chans_avail_list}]':\n{method_docstring}")
        methods_summary = "\n    ".join(all_methods_summaries) 
        if all_methods_summaries:
            methods_doc = f"\n\nAfter instantiation, the following methods will become available:\n{methods_summary}"
        else:
            methods_doc = ""
        full_instructions = f"{PREFACE}\n\n#{class_name}\n{class_docstring}\n Instantiation Documentation:\n\n{init_docstring}{methods_doc}\n\nTo instantiate, issue the required infomration with a message exactly in the following format: '{class_name}({params_str})'"
        return full_instructions

    async def on_invite(self, channel, by):
        if self.assigned_user is not None and by != self.assigned_user:
            await self.message(by, f"You are not my Santa!")
            try:
                await self.part(self.chan_free, "Hooray! I was chosen!")
                self.replace_free_self_if_needed()
            except:
                pass
            return
        if self.assigned_user is None: 
            self.assigned_user = by 
            # info = await self.whois(by)
            # self.user_full_name = info["realname"]
            await self.part(self.chan_free, "Hooray! I was chosen!")
            self.replace_free_self_if_needed()
        if len(self.joined_channels) > 0:
            await self.message(by, f"I'm already in a channel {self.joined_channels}, please join me there!")
            return
        await self.join(channel)
        if channel not in self.joined_channels: 
            self.joined_channels.append(channel)
        if not channel in self.invited_channels:
            self.invited_channels.append(channel)
        if self.obj is None:
            if not await self.try_instantiate(reply_target=by, source=by, invited_to=channel):
                await self.message(channel, self.instantiation_instructions())
        self.save_me()

    async def on_message(self, target, source, message):
        # don't respond to our own messages, as this leads to a positive feedback loop
        if source == self.nickname:
            log.debug(f"{self.nickname} Ignoring message from myself")
            return
        log.debug(f"{self.nickname} >>> INCOMING RAW target={target} source={source} me={self.nickname} assigned={self.assigned_user} message={message}")
        if target == self.chan_free:
            log.debug(f"{self.nickname} Ignoring message from #free_*")
            return
        if target == self.nickname:
            is_dm = True
            if self.assigned_user is None: 
                self.assigned_user = source
                info = await self.whois(source)
                # self.user_full_name = info["realname"]
                await self.part(self.chan_free, "Hooray! I was chosen!")
                self.replace_free_self_if_needed()
                self.save_me()
        else:
            is_dm = False
        
        # TODO HERE: user control??
        if is_dm and self.assigned_user != source:
            if "You are not my Santa" not in message:
                await self.message(source, f"You are not my Santa!")
                try:
                    await self.part(self.chan_free, "Hooray! I was chosen!")
                    self.replace_free_self_if_needed()
                except:
                    pass
            log.debug("DM Input from incorrect user")
            return
        
        if self.last_source is None:
            self.last_source = source
            self.last_target = target

        if self.last_source == source and self.last_target == target:
            log.debug(f"{self.nickname} Appending message to queue")
            self.messages.append((target, source, message, is_dm))
            if not self.waiting:
                self.waiting = True
                self.delayed_process_message_task = asyncio.create_task(self.delayed_on_message())
            if "Depth:" in message[-11:] and self.waiting:
                # TODO: multi-thread message processing
                self.delayed_process_message_task.cancel()
                self.waiting = False
                await self.process_messages()
        else:
            log.debug(f"{self.nickname} Processing messages immediately")
            await self.process_messages()
            self.last_source = source
            self.last_target = target
            self.messages = [(target, source, message, is_dm)]
            if not self.waiting:
                self.waiting = True
                asyncio.create_task(self.delayed_on_message())
        
    async def delayed_on_message(self):
        await asyncio.sleep(0.7)
        if self.messages:
            await self.process_messages()
        log.debug("Delayed message processing finished")
        self.waiting = False
    
    async def process_messages(self):
        log.debug(f"{self.nickname} Called: Processing messages")
        if len(self.messages) == 0:
            log.debug(f"{self.nickname} Ignoring empty messages")
            return

        my_messages = self.messages.copy()
        self.messages.clear()

        target = my_messages[0][0]
        source = my_messages[0][1]
        is_dm = my_messages[0][3]

        # join messages with a newline if the length less than 500 characters
        message = ""
        for m in my_messages:
            if len(m[2]) >= 402:
                message += m[2]
            else:
                message += "\n" + m[2]
        
        log.debug(f"{self.nickname} >>> REASSEMBLED MESSAGE target={target} source={source} me={self.nickname} assigned={self.assigned_user} message={message}")

        # extract "Depth: <number>" depth number from message using regex
        if "Depth:" in message:
            m = re.search(r'Depth: (\d+)', message)
            # extract depth number from match oject
            depth = int(m.group(1))
            # extract full matched text from match object
            full_match = m.group(0)
        else:
            depth = 0
            full_match = ""
        depth += 1

        if (self.last_sent_to == target or self.last_sent_to == source) and time.time() - self.last_sent_time < 0.3:
            self.last_sent_depth += 1
            depth = depth + self.last_sent_depth
        else:
            self.last_sent_to = None
            self.last_sent_depth = 0

        log.debug(f"{self.nickname} >>> REASSEMBLED INCOMING MESSAGE DEPTH {depth} target={target} source={source} me={self.nickname} assigned={self.assigned_user} message={message}")

        if depth > MAX_DEPTH:
            errmsg = f"{self.nickname} has max depth of {MAX_DEPTH} reached, not responding|" + message
            log.warning(errmsg)
            await self.message(self.assigned_user, errmsg)
            return
        message = message.replace(full_match, '')

        from_channel = target 
        if is_dm:
            from_channel = "private"
            participants = ""
        else:
            participants = " (current channel participants: "+",".join(self.channels[from_channel]["users"]) + ")"

        if target.startswith("#"):
            fallback_reply_target = target
        else:
            fallback_reply_target = source
        
        log.debug(f"{self.nickname} >>> THINKING target={target} source={source} me={self.nickname} assigned={self.assigned_user} message={message}")
        reply = await self.think(message, fallback_reply_target, source)
        if reply is None:
            log.debug(f"{self.nickname} >>> EMPTY REPLY target={target} source={source} me={self.nickname} assigned={self.assigned_user} message={message}")
            self.save_me()
            return
        log.debug(f"{self.nickname} >>> THINKING REPLY target={target} source={source} me={self.nickname} assigned={self.assigned_user} message={message} reply={reply}")

        if reply.startswith("TO: "):
            reply = "TO:"+reply[4:]
        if not "TO:" in reply and not "/part" in reply.lower() and not "/join" in reply.lower():
            reply = f"{self.nickname} ERROR: reply does not start with TO: <channel>|" + reply
            reply_target = self.assigned_user
            log.error(f"{self.nickname} >>> ERROR IN REPLY target={target} source={source} me={self.nickname} assigned={self.assigned_user} message={message} reply={reply}")
        else:
            reply_target = reply.split()[0]
            if not "TO:" in reply_target:
                reply = f"{self.nickname} ERROR: Could not parse reply that does start with TO: <channel>|" + reply
                reply_target = self.assigned_user
                log.error(f"{self.nickname} >>> ERROR IN REPLY target={target} source={source} me={self.nickname} assigned={self.assigned_user} message={message} reply={reply}")
            else:
                reply = reply.replace(reply_target, "").strip()
                reply_target = " ".join(reply_target.split()).strip()
                reply_target = reply_target.replace("TO:", "").strip()
        
        log.debug(f"{self.nickname} >>> REPLY target={target} source={source} me={self.nickname} assigned={self.assigned_user} message={message} reply={reply}")

        if "/JOIN #" in reply or "/join #" in reply or "/PART #" in reply or "/part #" in reply:
            # TODO: make sure in previous user message there was a request to join channel
            channel = None
            for word in reply.split(" "):
                word = word.strip()
                if word.startswith("#"):
                    channel = word
                    break
            if not channel: 
                log.debug("No channel to join")
                return
            channel = ''.join(ch for ch in channel if ch.isalnum() or ch in ('#', '_', '-'))
            if "join" in reply.lower():
                log.info(f"{self.nickname} >>> JOINING CHANNEL {channel}")
                if channel not in self.joined_channels:
                    self.joined_channels.append(channel)
                await self.join(channel)
                self.join_updated = False
            else:
                log.info(f"{self.nickname} >>> PARTING CHANNEL {channel}")
                if channel in self.joined_channels:
                    self.joined_channels.remove(channel)
                await self.part(channel)
                self.join_updated = False
            self.save_me()
            log.debug("Join completed")
            return

        log.debug(f"{self.nickname} >>> REPLYING TO TARGET {reply_target} source={source} me={self.nickname} assigned={self.assigned_user} message={message} reply={reply}")

        if reply_target == "private":
            reply_target = source
        if depth > MAX_DEPTH:
            log.warning(f"{self.nickname} >>> DEPTH EXCEEDED target={target} source={source} me={self.nickname} assigned={self.assigned_user} message={message} reply={reply}")
        else:
            log.debug(f"{self.nickname} >>> REPLYING TO TARGET {reply_target} source={source} me={self.nickname} assigned={self.assigned_user} message={message} reply={reply}")
            ready_msg = f"{reply} Depth: {depth}"
            if reply_target.startswith("#"):
                if not reply_target in self.joined_channels:
                    log.warn("WARNING: tried sending to channel {reply_target} which I have not joined, joining")
                    log.info(f"{self.nickname} >>> JOINING CHANNEL {reply_target}")
                    try:
                        await self.join(channel)
                        self.joined_channels.append(channel)
                        self.join_updated = False
                    except Exception as e:
                        log.error(f"{self.nickname} Tried to join channel {reply_target} but failed: {e}")
            await self.message(reply_target.strip(), ready_msg)
        self.save_me()
        self.last_source = None
        self.last_target = None
    
    def save_me(self):
        if self.save_path is not None:
            full_save_path = os.path.join(self.save_path, f'{self.nickname}.pickle')
            log.debug(f"{self.nickname} >>> SAVING SELF TO {full_save_path}")
            try:
                with open(full_save_path, 'wb+') as f:
                    pickle.dump(self, f)
            except Exception as e:
                log.error(f"{self.nickname} >>> ERROR SAVING SELF TO {full_save_path}: {e}")
    
    def remove_me(self):
        if self.save_path is not None:
            full_save_path = os.path.join(self.save_path, f'{self.nickname}.pickle')
            log.debug(f"{self.nickname} >>> REMOVING SELF FROM {full_save_path}")
            try:
                os.remove(full_save_path)
            except Exception as e:
                log.error(f"{self.nickname} >>> ERROR REMOVING SELF FROM {full_save_path}: {e}")
    
    def is_superuser(self, source_user, source_channel=None):
        # TODO: check if he's op on the channel with originating message
        return source_user == self.assigned_user
    
    async def think(self, message, reply_target, source):
        # parse message in format "function_name(arg1, arg2, arg3)"
        # check if format is correct with regex
        # pattern = r'([a-zA-Z_][A-Za-z0-9_]*)\("([^"]+)"\)'
        pattern = r'([a-zA-Z_][A-Za-z0-9_]*)\((.*)\)'
        match = re.search(pattern, message)
        usage_error_message = "Error in processing request, because the command format wasn't recognized."
        try:
            if match is not None and len(match.groups()) == 2:
                func_name = match[1]
                if func_name == "destroy" and self.is_superuser(source, source_channel=reply_target):
                    log.info(f"Destroying bot {self.nickname} by request from {source}")
                    await self.message(reply_target, f"Destroying bot {self.nickname} by request from {source}")
                    for channel in self.joined_channels:
                        try:
                            await self.part(channel)
                        except:
                            pass
                    self.joined_channels = []
                    self.pool.bot_instances.remove(self)
                    self.pool = None
                    self.remove_me()
                    for task in self.tasks:
                        if not task.cancelled():
                            task.cancel()
                    return None
                s_args = match[2]
                if not self.check_reply_channel(func_name, reply_target):
                    log.info(f"Reply channel not allowed for function: {func_name}")
                    await self.message(reply_target, f"The function {func_name} is not allowed to reply to channel {reply_target}. It is available on channel(s): {', '.join(self.list_exported_channels(func_name))}")
                    return

                if func_name != self.class_.__name__ and self.obj is None:
                    if not await self.try_instantiate(reply_target=reply_target, source=source, invited_to=self.joined_channels):
                        await self.message(reply_target, self.instantiation_instructions())
                        return
                try:
                    args = json.loads("["+s_args+"]")
                except Exception as e:
                    log.info(f"{self.nickname} >>> ERROR IN JSON LOADS {e}")
                    raise UsageError("Error in processing request, because arguments could not be parsed. The arguments must be in Python language format (or JSON).")
                if hasattr(self.obj, func_name) or func_name == self.class_.__name__:
                    if func_name == self.class_.__name__:
                        func = self.class_
                        sig = inspect.signature(self.class_.__init__)
                    else:
                        func = getattr(self.obj, func_name)
                        sig = inspect.signature(func)
                    kwargs = {}
                    if "nickname" in sig.parameters:
                        kwargs["nickname"] = self.nickname
                    if "assigned_user" in sig.parameters:
                        kwargs["assigned_user"] = self.assigned_user
                    if "target" in sig.parameters:
                        kwargs["target"] = reply_target
                    if "source" in sig.parameters:
                        kwargs["source"] = source
                    if "invited_to" in sig.parameters:
                        kwargs["invited_to"] = self.joined_channels 

                    if inspect.iscoroutinefunction(func):
                        log.debug(f"Method {func_name} is coroutine")
                        if "send" in sig.parameters:
                            kwargs["send"] = self.message 
                        log.debug(f"Method {func_name} is infinite loop - starting task")
                        event_loop = self.pool.eventloop
                        self.tasks.append(event_loop.create_task(func(**kwargs)))
                    elif callable(func):
                        try:
                            result = func(*args, **kwargs)
                            if func_name == self.class_.__name__:
                                if self.obj is not None:
                                    log.debug("Already instantiated")
                                    raise UsageError(f"You tried to instantiate an already instantiated class bot. Please use as described.")
                                log.debug("Instantiated successfully")
                                self.obj = result
                                await self.join_supplemental_channels()
                                self.save_me()
                                return f"TO:{reply_target} {self.instantiation_success_message()}"
                            if result is None: return None
                            if isinstance(result, str):
                                return f"TO:{reply_target} {result}"
                            else:  # try json load
                                try:
                                    result = json.dumps(result)
                                    return f"TO:{reply_target} {result}"
                                except Exception as e:
                                    # means binary, so try base64 of pickle
                                    try:
                                        result = pickle.dumps(result)
                                        result = base64.b64encode(result).decode()
                                        return f"TO:{reply_target} {result}"
                                    except Exception as e:
                                        log.error(f"{self.nickname} >>> ERROR IN THINK - REPLY SERIALIZATION ERROR target={reply_target} message={message} func_name={func_name} args={args} exception={e}")
                                        import traceback
                                        traceback.print_exc()
                                        return f"TO:{reply_target} REPLY SERIALIZATION ERROR: {e}"
                        except UsageError as e:
                            raise e
                        except Exception as e:
                            log.error(f"{self.nickname} >>> ERROR IN THINK - CALL ERROR target={reply_target} message={message} func_name={func_name} args={args} exception={e}")
                            import traceback
                            traceback.print_exc()
                            return f"TO:{reply_target} CALL ERROR: {e}"
                    else:
                        log.error(f"ERROR IN THINK - NOT CALLABLE: {func_name} - {func}")
                        return f"TO:{reply_target} supplied symbol name, while exists on the target object, is not callable: {func_name}"
            else:
                log.debug(f"INCORRECT CALL: {message}")
                raise UsageError(usage_error_message)
        except UsageError as e:
            usage_error_message = str(e)
        except:
            import traceback
            traceback.print_exc()
            log.error(f"{self.nickname} >>> ERROR IN THINK - INCORRECT CALL target={reply_target} message={message} exception={traceback.format_exc()}")
        log.info(f"THINK - INCORRECT CALL: {message}, returning docs")
        if self.obj is None:
            log.info(f"THINK - NOT INSTANTIATED: {message}, trying to instantiate")
            if not await self.try_instantiate(reply_target=reply_target, source=source, invited_to=self.joined_channels):
                await self.message(reply_target, self.instantiation_instructions())
                return
        # prepare a full description of the object
        if reply_target.startswith("#"):
            channel = reply_target
        else:
            channel = None
        doc = self.get_documentation(preface=usage_error_message, channel=channel)
        return f"TO:{reply_target} {doc}"
    
    def instantiation_success_message(self):
        doc = self.get_documentation(preface="Successfully instantiated! Now you can use the bot according to the following documentation:\n")
        return doc
    
    def get_documentation(self, preface="", channel=None):
        obj_class_docstring = self.obj.__doc__ or "Should be self-explanatory"
        # list all methods of object and their docstrings
        all_methods_docstrings = []
        for method_name in dir(self.obj):
            if method_name.startswith("_"):
                continue
            method = getattr(self.obj, method_name)
            if callable(method):
                if channel is not None and not self.check_reply_channel(method_name, channel):
                    log.debug(f"DOC Skipping method {method_name} because it is not exported to channel {channel}")
                    continue
                if "while True:" in inspect.getsource(method):
                    log.debug(f"DOC Skipping method {method_name} because it is infinite loop")
                    continue
                log.debug("Found method: " + method_name)
                method_docstring = method.__doc__ or "Should be self-explanatory"
                # get method signature
                method_signature = inspect.signature(method)
                params = list(method_signature.parameters.keys())
                params = self.clean_params(params)
                params_string = ", ".join([f"<{p}>" for p in params])
                chans_avail_list = ", ".join(self.func_chan_avail(method_name))
                all_methods_docstrings.append(f"Method '{method_name}({params_string}) [available on channel(s): {chans_avail_list}]': {method_docstring}")

        all_methods_docstrings.append(f"Method 'destroy()': Delete and unload this bot.")
        
        doc = f"{preface} In order to provide the correct request you must send the message exactly in the format of '<method_name>(<argument1>, <argument2>, ...)'. Arguments must be in Python or JSON format, and available methods and documentation are:\n# {self.class_.__name__}\n {obj_class_docstring}\n\nMethods:\n" + "\n".join(all_methods_docstrings) + "\nThis bot has potentially other methods available on channel(s): " + ", ".join(self.joined_channels)
        return doc
    
    def check_reply_channel(self, method_name, channel):
        allowed_channels = self.list_exported_channels(method_name)
        log.debug(f"Checking if channel {channel} is allowed for method {method_name} with allowed channels {allowed_channels}")
        if len(allowed_channels) == 0:
            return True
        if channel in allowed_channels:
            return True
        return False 
    
    def list_exported_channels(self, method_name):
        if method_name in self.class_._export_metadata:
            chans_base = self.class_._export_metadata[method_name]["channel"]
            if len(self.joined_channels) > 0:
                chans_sup = [self.invited_channels[0] + x for x in self.class_._export_metadata[method_name]["channel_suffix"]]
            log.debug(f"Listing exported channels for method {method_name}: {chans_base + chans_sup}")
            return chans_base + chans_sup
        else:
            return self.invited_channels[:1]
    
    def func_chan_avail(self, func_name):
        """Return the list of channels where the function is available."""
        if func_name in self.class_._export_metadata:
            chans_base = self.class_._export_metadata[func_name]["channel"]
            if len(self.joined_channels) > 0:
                chans_sup = [self.invited_channels[0] + x for x in self.class_._export_metadata[func_name]["channel_suffix"]]
            return chans_base + chans_sup
        else:
            return self.invited_channels[:1]


def check_defaults_func(func):
    sig = inspect.signature(func)
    params = list(sig.parameters.keys())
    now_defaults = False
    prev_par = None
    for p in params:
        if p not in AUTO_PARAMS and not now_defaults:
            prev_par = p
            continue
        else:
            now_defaults = True
        if now_defaults and not p in AUTO_PARAMS:
            raise TypeError(f"Parameter {p} is not allowed in current position in the function {func.__name__} because it follows {prev_par} which is a special parameter name within kwarg parameter names {AUTO_PARAMS} that are automatically filled in by calling bot machinery and should only follow after all non-auto parameters.")
        prev_par = p


# TODO: object-oriented interface to have more control over the export runtimes
def export(obj_or_class_or_method, server_address="irc.magic-r.com", server_port=3389, channel=DEFAULT_CHANNEL, channel_suffix="", nickname_base=None, use_tls=False, tls_verify=False, base_path="./nllink.data", full_storage_path=None, blocking=None):
    # TODO: bot can only join one main channel, subsequent invites must fail
    """Superfunction to export a Python class to IRC as a bot in Magic-R natural language format.

    Args:
        obj_or_class_or_method (any object or class or method): object or class or method to export. The class must contain docstrings for all methods and attributes, including __init__. It must be picklable. The explanations must be as thorough and as grounded as possible, as they will be used to generate the natural language interface. If class method is supplied - it will be used to set attributes of the method, like channel_suffix or channel. If class is supplied - it will be instantiated with the default __init__ method. If object is supplied - it will be used as-is.
        server_address (str, optional): IRC server address. Defaults to "irc.magic-r.com".
        server_port (int, optional): IRC server port. Defaults to 3389.
        channel (str, optional): IRC channel to put "free" bots to. Defaults to "#export_bots".
        channel_suffix (str, optional): Suffix to add to the channel name when exporting specific function. Defaults to "".
        nickname_base (str, optional): Base for the bot nicknames + 0001, ..2, ... Defaults to object's class name lowercase.
        use_tls (bool, optional): Use TLS for server connection or not. Defaults to False.
        tls_verify (bool, optional): Use TLS cert verification or not. Defaults to False.
        base_path (str, optional): Base locally-attached filesystem path for bot instances binaries storage. Defaults to [current working directory]/nllink.data/[object's class name].
        full_storage_path (str, optional): If provided, will be used as a full path to locally-attached filesystem storage for all object instances without subdirectories. Defaults to base_path/[objects's class names].
    
    Notes:
        If a method in class is defined as async, it will be started within internal async loop as a separate task, and "send" argument provided with a function to send messages to a channel or user with signature 'send(target, message)'. This can be useful for infinite loops.

    Returns:
        ClientPool, Tuple[List[bot_instances], thread]: ClientPool with eventloop, List of bot instances and a thread that runs the IRC event loop. 
    
    Example:
        >>> class MyBot:
        >>>     def __init__(self, nickname, assigned_user=None, target=None, source=None, invited_to=None):
        >>>         self.nickname = nickname
        >>>         # ...
        >>>     def my_method(self, arg1, arg2):
        >>>         # ...
        >>>         return "result"
        >>>     def my_method2(self, arg1, arg2):
        >>>         # ...
        >>> export(MyBot.my_method, channel_suffix="_meth")
        >>> export(MyBot.my_method2, channel="#my_channel_method2")
        >>> export(MyBot, channel="#my_channel")
    """    

    if channel and not channel.startswith("#"):
        channel = "#" + channel

    # explore 3 options: class, classmethod, object
    if isinstance(obj_or_class_or_method, type):
        cls = obj_or_class_or_method
        cls_name = obj_or_class_or_method.__name__
        method  = None
        check_defaults_func(cls.__init__)
        for method_name in dir(cls):
            if method_name.startswith("_"):
                continue
            method = getattr(cls, method_name)
            if callable(method) and not isinstance(method, type):
                check_defaults_func(method)
    elif callable(obj_or_class_or_method) and not isinstance(obj_or_class_or_method, type):
        # this is class un-bound method, need to extract class that this method belongs to
        # since it doesn't have __self__ yet, we use different method:
        cls = inspect._findclass(obj_or_class_or_method)
        cls_name = cls.__name__
        method = obj_or_class_or_method
        check_defaults_func(method)
    elif isinstance(obj_or_class_or_method, object):
        cls = obj_or_class_or_method.__class__
        cls_name = cls.__name__
        method = None

    if not hasattr(cls, "_export_metadata"):
        cls._export_metadata = defaultdict(lambda: defaultdict(list))

    if callable(obj_or_class_or_method) and not isinstance(obj_or_class_or_method, type):
        # just set the metadata and return (if blocking=False) - TODO: this is the only supported mode as of yet
        if channel != DEFAULT_CHANNEL:
            cls._export_metadata[obj_or_class_or_method.__name__]["channel"].append(channel)
        if channel_suffix != "":
            cls._export_metadata[obj_or_class_or_method.__name__]["channel_suffix"].append(channel_suffix)
        return

    full_path = full_storage_path or str(os.path.join(base_path, cls_name))
    Path(full_path).mkdir(parents=True, exist_ok=True)
    bot_instances = []
    max_bot_id = 0
    if nickname_base is None:
        nickname_base = cls_name.lower()

    free_count = 0
    # iterate over files in data directory with "*.pickle" pattern
    log.debug(f"Looking for bot instances in {full_path}")
    for file in glob.glob(os.path.join(full_path, "*.pickle")):
        # extract elf name from file name in form of "elf00001.pickle"
        bot_name = file.split("/")[-1].split(".")[0]
        # extract elf id from file name using regex of numbers
        try:
            bot_id = int(re.findall(r'\d+', bot_name)[0])
        except IndexError:
            continue
        # load elf state from file
        with open(file, 'rb') as f:
            client: IRCExportBot = pickle.load(f)
            # check if elf is active
            bot_instances.append(client)
        max_bot_id = max(max_bot_id, bot_id)
        if client.assigned_user is None:
            free_count += 1
    log.info(f"Found {len(bot_instances)} bot instances in storage, {free_count} of them are free")

    pool = pydle.ClientPool()

    # now create additional free zoo
    for _ in range(max_bot_id + 1, max_bot_id + 1 + 10):
        _ = str(_).zfill(5)
        new_bot_name = f"{nickname_base}{_}"
        new_bot_real_name = f"{cls_name} {_}"
        bot_new_client = IRCExportBot(new_bot_name, obj=obj_or_class_or_method, chan_free=channel, realname=new_bot_real_name)
        bot_instances.append(bot_new_client)

    bot: IRCExportBot
    for bot in bot_instances:
        pool.connect(bot, server_address, port=server_port, tls=use_tls, tls_verify=tls_verify)
        bot.pool = pool
        bot.save_path = full_path
    pool.next_id = max_bot_id + 1 + 10
    pool.bot_instances = bot_instances

    if blocking:
        pool.handle_forever()
    else:
        thread = threading.Thread(target=pool.handle_forever)
        thread.start()
    return pool, bot_instances, thread