#  tgcalls - a Python binding for C++ library by Telegram
#  pytgcalls - a library connecting the Python binding with MTProto
#  Copyright (C) 2020-2021 Il`ya (Marshal) <https://github.com/MarshalX>
#
#  This file is part of tgcalls and pytgcalls.
#
#  tgcalls and pytgcalls is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Lesser General Public License as published
#  by the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  tgcalls and pytgcalls is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License v3
#  along with tgcalls. If not, see <http://www.gnu.org/licenses/>.

from asyncio import BaseEventLoop
from typing import List

from telethon.errors import (
    GroupcallJoinMissingError,
    BadRequestError as TelethonBadRequest,
    GroupcallSsrcDuplicateMuchError as TelethonGroupcallSsrcDuplicateMuch,
)
from telethon import events
from telethon.tl import functions, types
from telethon.tl.types import GroupCallDiscarded as TelethonGroupCallDiscarded, InputPeerChannel, InputPeerChat

from pytgcalls.mtproto.data import GroupCallDiscardedWrapper, GroupCallWrapper, ParticipantWrapper
from pytgcalls.mtproto.data.update import UpdateGroupCallWrapper, UpdateGroupCallParticipantsWrapper
from pytgcalls.mtproto.exceptions import BadRequest, GroupcallSsrcDuplicateMuch
from pytgcalls.utils import int_ssrc

from telethon import TelegramClient

from pytgcalls.mtproto import MTProtoBridgeBase


class TelethonBridge(MTProtoBridgeBase):
    def __init__(self, client: TelegramClient):
        super().__init__(client)

        self._update_to_handler = {
            types.UpdateGroupCallParticipants: self._process_group_call_participants_update,
            types.UpdateGroupCall: self._process_group_call_update,
        }

        self._handler_group = None
        self._update_handler = events.Raw(self._process_update)

    async def _process_update(self, _, update, users, chats):
        if type(update) not in self._update_to_handler.keys():  # TODO or not self.__native_instance:
            return

        if not self.group_call or not update.call or update.call.id != self.group_call.id:
            return
        self.group_call = update.call
        await self._update_to_handler[type(update)](update)

    async def _process_group_call_participants_update(self, update):
        participants = [ParticipantWrapper(p.source, p.left, p.peer) for p in update.participants]
        wrapped_update = UpdateGroupCallParticipantsWrapper(participants)

        await self.group_call_participants_update_callback(wrapped_update)

    async def _process_group_call_update(self, update):
        if isinstance(update.call, TelethonGroupCallDiscarded):
            call = GroupCallDiscardedWrapper()  # no info needed
        else:
            call = GroupCallWrapper(update.call.id, update.call.params)

        wrapped_update = UpdateGroupCallWrapper(update.chat_id, call)

        await self.group_call_update_callback(wrapped_update)

    async def check_group_call(self) -> bool:
        if not self.group_call or not self.my_ssrc:
            return False

        try:
            in_group_call = await (
                self.client(functions.phone.CheckGroupCallRequest(call=self.group_call, source=int_ssrc(self.my_ssrc)))
            )
        except TelethonBadRequest as e:
            if not isinstance(e, GroupcallJoinMissingError):
                raise BadRequest(e.x)

            in_group_call = False

        return in_group_call

    async def get_group_call_participants(self) -> List['ParticipantWrapper']:
        _participants = (
            await (self.client(functions.phone.GetGroupCallRequest(call=self.full_chat.call)))
        ).participants
        wrapped_participants = [ParticipantWrapper(p.source, p.left, p.peer) for p in _participants]
        return wrapped_participants

    async def leave_current_group_call(self):
        if not self.full_chat.call or not self.my_ssrc:
            return

        response = await self.client(
            functions.phone.LeaveGroupCallRequest(call=self.full_chat.call, source=int_ssrc(self.my_ssrc))
        )
        await self.client.handle_updates(response)

    async def edit_group_call_member(self, peer, volume: int = None, muted=False):
        response = await self.client(
            functions.phone.EditGroupCallParticipantRequest(
                call=self.full_chat.call,
                participant=peer,
                muted=muted,
                volume=volume,
            )
        )
        await self.client.handle_updates(response)

    async def get_and_set_self_peer(self):
        self.my_peer = await self.client.get_input_peer("me")

        return self.my_peer


    async def get_and_set_group_call(self, group):
        """Get group call input of chat.
        Args:
            group (`InputPeerChannel` | `InputPeerChat` | `str` | `int`): Chat ID in any form.
        Returns:
            `InputGroupCall`.
        """

        self.chat_peer = group

        if isinstance(self.chat_peer, InputPeerChannel):
            self.full_chat = (
                await (self.client(functions.channels.GetFullChannelRequest(channel=self.chat_peer)))
            ).full_chat
        elif isinstance(self.chat_peer, InputPeerChat):
            self.full_chat = (
                await (self.client(functions.messages.GetFullChatRequest(chat_id=self.chat_peer.chat_id)))
            ).full_chat

        if self.full_chat is None:
            raise RuntimeError(f'Can\'t get full chat by {group}')

        self.group_call = self.full_chat.call

        return self.group_call

    def unregister_update_handlers(self):
        if self._handler_group:
            self.client.remove_event_handler(self._update_handler, self._handler_group)
            self._handler_group = None

    def register_update_handlers(self):
        if self.group_call.id > 0:
            self._handler_group = -self.group_call.id
        self._handler_group = self.group_call.id

        self.client.add_event_handler(self._update_handler, self._handler_group)

    async def resolve_and_set_join_as(self, join_as):
        my_peer = await self.get_and_set_self_peer()

        if join_as is None:
            self.join_as = my_peer
        else:
            self.join_as = join_as

    async def send_speaking_group_call_action(self):
        await self.client(
            functions.messages.SetTypingRequest(peer=self.chat_peer, action=types.SpeakingInGroupCallAction())
        )

    async def join_group_call(self, invite_hash: str, params: str, muted: bool):
        try:
            response = await self.client(
                functions.phone.JoinGroupCallRequest(
                    call=self.group_call,
                    join_as=self.join_as,
                    invite_hash=invite_hash,
                    params=types.DataJSON(data=params),
                    muted=muted,
                )
            )

            await self.client.handle_updates(response)
        except TelethonGroupcallSsrcDuplicateMuch as e:
            raise GroupcallSsrcDuplicateMuch(e.x)

    def get_event_loop(self) -> BaseEventLoop:
        return self.client.loop