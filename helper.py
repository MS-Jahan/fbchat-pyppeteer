import logging
from fbchat import *
from fbchat.models import *
from fbchat._util import *
from fbchat import _graphql
from fbchat._state import State

import time
import asyncio
from pyppeteer import launch
import base64
# from helper import _parse_message
import json
from json import JSONDecodeError
import traceback
import string
import codecs

# Log settings
log = logging.getLogger("client")
log.setLevel(logging.DEBUG)
# Creates the console handler
handler = logging.StreamHandler()
log.addHandler(handler)

class Client(object):
    
    """
    INTERNAL REQUEST METHODS
    """

    def _get(self, url, params):
        return self._state._get(url, params)

    def _post(self, url, params, files=None):
        return self._state._post(url, params, files=files)

    def _payload_post(self, url, data, files=None):
        return self._state._payload_post(url, data, files=files)

    def graphql_requests(self, *queries):
        """Execute GraphQL queries.

        Args:
            queries (dict): Zero or more dictionaries

        Returns:
            tuple: A tuple containing JSON GraphQL queries

        Raises:
            FBchatException: If request failed
        """
        return tuple(self._state._graphql_requests(*queries))

    def graphql_request(self, query):
        """Shorthand for ``graphql_requests(query)[0]``.

        Raises:
            FBchatException: If request failed
        """
        return self.graphql_requests(query)[0]

    """
    END INTERNAL REQUEST METHODS
    """

    """
    DEFAULT THREAD METHODS
    """

    def _getThread(self, given_thread_id=None, given_thread_type=None):
        """Check if thread ID is given and if default is set, and return correct values.

        Returns:
            tuple: Thread ID and thread type

        Raises:
            ValueError: If thread ID is not given and there is no default
        """
        if given_thread_id is None:
            if self._default_thread_id is not None:
                return self._default_thread_id, self._default_thread_type
            else:
                raise ValueError("Thread ID is not set")
        else:
            return given_thread_id, given_thread_type

    def setDefaultThread(self, thread_id, thread_type):
        """Set default thread to send messages to.

        Args:
            thread_id: User/Group ID to default to. See :ref:`intro_threads`
            thread_type (ThreadType): See :ref:`intro_threads`
        """
        self._default_thread_id = thread_id
        self._default_thread_type = thread_type

    def resetDefaultThread(self):
        """Reset default thread."""
        self.setDefaultThread(None, None)

    """
    END DEFAULT THREAD METHODS
    """

    """
    FETCH METHODS
    """

    def _forcedFetch(self, thread_id, mid):
        params = {"thread_and_message_id": {"thread_id": thread_id, "message_id": mid}}
        (j,) = self.graphql_requests(_graphql.from_doc_id("1768656253222505", params))
        return j

    def fetchThreads(self, thread_location, before=None, after=None, limit=None):
        """Fetch all threads in ``thread_location``.

        Threads will be sorted from newest to oldest.

        Args:
            thread_location (ThreadLocation): INBOX, PENDING, ARCHIVED or OTHER
            before: Fetch only thread before this epoch (in ms) (default all threads)
            after: Fetch only thread after this epoch (in ms) (default all threads)
            limit: The max. amount of threads to fetch (default all threads)

        Returns:
            list: :class:`Thread` objects

        Raises:
            FBchatException: If request failed
        """
        threads = []

        last_thread_timestamp = None
        while True:
            # break if limit is exceeded
            if limit and len(threads) >= limit:
                break

            # fetchThreadList returns at max 20 threads before last_thread_timestamp (included)
            candidates = self.fetchThreadList(
                before=last_thread_timestamp, thread_location=thread_location
            )

            if len(candidates) > 1:
                threads += candidates[1:]
            else:  # End of threads
                break

            last_thread_timestamp = threads[-1].last_message_timestamp

            # FB returns a sorted list of threads
            if (before is not None and int(last_thread_timestamp) > before) or (
                after is not None and int(last_thread_timestamp) < after
            ):
                break

        # Return only threads between before and after (if set)
        if before is not None or after is not None:
            for t in threads:
                last_message_timestamp = int(t.last_message_timestamp)
                if (before is not None and last_message_timestamp > before) or (
                    after is not None and last_message_timestamp < after
                ):
                    threads.remove(t)

        if limit and len(threads) > limit:
            return threads[:limit]

        return threads

    def fetchAllUsersFromThreads(self, threads):
        """Fetch all users involved in given threads.

        Args:
            threads: Thread: List of threads to check for users

        Returns:
            list: :class:`User` objects

        Raises:
            FBchatException: If request failed
        """
        users = []
        users_to_fetch = []  # It's more efficient to fetch all users in one request
        for thread in threads:
            if thread.type == ThreadType.USER:
                if thread.uid not in [user.uid for user in users]:
                    users.append(thread)
            elif thread.type == ThreadType.GROUP:
                for user_id in thread.participants:
                    if (
                        user_id not in [user.uid for user in users]
                        and user_id not in users_to_fetch
                    ):
                        users_to_fetch.append(user_id)
        for user_id, user in self.fetchUserInfo(*users_to_fetch).items():
            users.append(user)
        return users

    def fetchAllUsers(self):
        """Fetch all users the client is currently chatting with.

        Returns:
            list: :class:`User` objects

        Raises:
            FBchatException: If request failed
        """
        data = {"viewer": self._uid}
        j = self._payload_post("/chat/user_info_all", data)

        users = []
        for data in j.values():
            if data["type"] in ["user", "friend"]:
                if data["id"] in ["0", 0]:
                    # Skip invalid users
                    continue
                users.append(User._from_all_fetch(data))
        return users

    def searchForUsers(self, name, limit=10):
        """Find and get users by their name.

        Args:
            name: Name of the user
            limit: The max. amount of users to fetch

        Returns:
            list: :class:`User` objects, ordered by relevance

        Raises:
            FBchatException: If request failed
        """
        params = {"search": name, "limit": limit}
        (j,) = self.graphql_requests(_graphql.from_query(_graphql.SEARCH_USER, params))

        return [User._from_graphql(node) for node in j[name]["users"]["nodes"]]

    def searchForPages(self, name, limit=10):
        """Find and get pages by their name.

        Args:
            name: Name of the page

        Returns:
            list: :class:`Page` objects, ordered by relevance

        Raises:
            FBchatException: If request failed
        """
        params = {"search": name, "limit": limit}
        (j,) = self.graphql_requests(_graphql.from_query(_graphql.SEARCH_PAGE, params))

        return [Page._from_graphql(node) for node in j[name]["pages"]["nodes"]]

    def searchForGroups(self, name, limit=10):
        """Find and get group threads by their name.

        Args:
            name: Name of the group thread
            limit: The max. amount of groups to fetch

        Returns:
            list: :class:`Group` objects, ordered by relevance

        Raises:
            FBchatException: If request failed
        """
        params = {"search": name, "limit": limit}
        (j,) = self.graphql_requests(_graphql.from_query(_graphql.SEARCH_GROUP, params))

        return [Group._from_graphql(node) for node in j["viewer"]["groups"]["nodes"]]

    def searchForThreads(self, name, limit=10):
        """Find and get threads by their name.

        Args:
            name: Name of the thread
            limit: The max. amount of groups to fetch

        Returns:
            list: :class:`User`, :class:`Group` and :class:`Page` objects, ordered by relevance

        Raises:
            FBchatException: If request failed
        """
        params = {"search": name, "limit": limit}
        (j,) = self.graphql_requests(
            _graphql.from_query(_graphql.SEARCH_THREAD, params)
        )

        rtn = []
        for node in j[name]["threads"]["nodes"]:
            if node["__typename"] == "User":
                rtn.append(User._from_graphql(node))
            elif node["__typename"] == "MessageThread":
                # MessageThread => Group thread
                rtn.append(Group._from_graphql(node))
            elif node["__typename"] == "Page":
                rtn.append(Page._from_graphql(node))
            elif node["__typename"] == "Group":
                # We don't handle Facebook "Groups"
                pass
            else:
                log.warning(
                    "Unknown type {} in {}".format(repr(node["__typename"]), node)
                )

        return rtn

    def searchForMessageIDs(self, query, offset=0, limit=5, thread_id=None):
        """Find and get message IDs by query.

        Args:
            query: Text to search for
            offset (int): Number of messages to skip
            limit (int): Max. number of messages to retrieve
            thread_id: User/Group ID to search in. See :ref:`intro_threads`

        Returns:
            typing.Iterable: Found Message IDs

        Raises:
            FBchatException: If request failed
        """
        thread_id, thread_type = self._getThread(thread_id, None)

        data = {
            "query": query,
            "snippetOffset": offset,
            "snippetLimit": limit,
            "identifier": "thread_fbid",
            "thread_fbid": thread_id,
        }
        j = self._payload_post("/ajax/mercury/search_snippets.php?dpr=1", data)

        result = j["search_snippets"][query]
        snippets = result[thread_id]["snippets"] if result.get(thread_id) else []
        for snippet in snippets:
            yield snippet["message_id"]

    def searchForMessages(self, query, offset=0, limit=5, thread_id=None):
        """Find and get `Message` objects by query.

        Warning:
            This method sends request for every found message ID.

        Args:
            query: Text to search for
            offset (int): Number of messages to skip
            limit (int): Max. number of messages to retrieve
            thread_id: User/Group ID to search in. See :ref:`intro_threads`

        Returns:
            typing.Iterable: Found :class:`Message` objects

        Raises:
            FBchatException: If request failed
        """
        message_ids = self.searchForMessageIDs(
            query, offset=offset, limit=limit, thread_id=thread_id
        )
        for mid in message_ids:
            yield self.fetchMessageInfo(mid, thread_id)

    def search(self, query, fetch_messages=False, thread_limit=5, message_limit=5):
        """Search for messages in all threads.

        Args:
            query: Text to search for
            fetch_messages: Whether to fetch :class:`Message` objects or IDs only
            thread_limit (int): Max. number of threads to retrieve
            message_limit (int): Max. number of messages to retrieve

        Returns:
            typing.Dict[str, typing.Iterable]: Dictionary with thread IDs as keys and iterables to get messages as values

        Raises:
            FBchatException: If request failed
        """
        data = {"query": query, "snippetLimit": thread_limit}
        j = self._payload_post("/ajax/mercury/search_snippets.php?dpr=1", data)
        result = j["search_snippets"][query]

        if not result:
            return {}

        if fetch_messages:
            search_method = self.searchForMessages
        else:
            search_method = self.searchForMessageIDs

        return {
            thread_id: search_method(query, limit=message_limit, thread_id=thread_id)
            for thread_id in result
        }

    def _fetchInfo(self, *ids):
        data = {"ids[{}]".format(i): _id for i, _id in enumerate(ids)}
        j = self._payload_post("/chat/user_info/", data)

        if j.get("profiles") is None:
            raise FBchatException("No users/pages returned: {}".format(j))

        entries = {}
        for _id in j["profiles"]:
            k = j["profiles"][_id]
            if k["type"] in ["user", "friend"]:
                entries[_id] = {
                    "id": _id,
                    "type": ThreadType.USER,
                    "url": k.get("uri"),
                    "first_name": k.get("firstName"),
                    "is_viewer_friend": k.get("is_friend"),
                    "gender": k.get("gender"),
                    "profile_picture": {"uri": k.get("thumbSrc")},
                    "name": k.get("name"),
                }
            elif k["type"] == "page":
                entries[_id] = {
                    "id": _id,
                    "type": ThreadType.PAGE,
                    "url": k.get("uri"),
                    "profile_picture": {"uri": k.get("thumbSrc")},
                    "name": k.get("name"),
                }
            else:
                raise FBchatException(
                    "{} had an unknown thread type: {}".format(_id, k)
                )

        log.debug(entries)
        return entries

    def fetchUserInfo(self, *user_ids):
        """Fetch users' info from IDs, unordered.

        Warning:
            Sends two requests, to fetch all available info!

        Args:
            user_ids: One or more user ID(s) to query

        Returns:
            dict: :class:`User` objects, labeled by their ID

        Raises:
            FBchatException: If request failed
        """
        threads = self.fetchThreadInfo(*user_ids)
        users = {}
        for id_, thread in threads.items():
            if thread.type == ThreadType.USER:
                users[id_] = thread
            else:
                raise FBchatUserError("Thread {} was not a user".format(thread))

        return users

    def fetchPageInfo(self, *page_ids):
        """Fetch pages' info from IDs, unordered.

        Warning:
            Sends two requests, to fetch all available info!

        Args:
            page_ids: One or more page ID(s) to query

        Returns:
            dict: :class:`Page` objects, labeled by their ID

        Raises:
            FBchatException: If request failed
        """
        threads = self.fetchThreadInfo(*page_ids)
        pages = {}
        for id_, thread in threads.items():
            if thread.type == ThreadType.PAGE:
                pages[id_] = thread
            else:
                raise FBchatUserError("Thread {} was not a page".format(thread))

        return pages

    def fetchGroupInfo(self, *group_ids):
        """Fetch groups' info from IDs, unordered.

        Args:
            group_ids: One or more group ID(s) to query

        Returns:
            dict: :class:`Group` objects, labeled by their ID

        Raises:
            FBchatException: If request failed
        """
        threads = self.fetchThreadInfo(*group_ids)
        groups = {}
        for id_, thread in threads.items():
            if thread.type == ThreadType.GROUP:
                groups[id_] = thread
            else:
                raise FBchatUserError("Thread {} was not a group".format(thread))

        return groups

    def fetchThreadInfo(self, *thread_ids):
        """Fetch threads' info from IDs, unordered.

        Warning:
            Sends two requests if users or pages are present, to fetch all available info!

        Args:
            thread_ids: One or more thread ID(s) to query

        Returns:
            dict: :class:`Thread` objects, labeled by their ID

        Raises:
            FBchatException: If request failed
        """
        queries = []
        for thread_id in thread_ids:
            params = {
                "id": thread_id,
                "message_limit": 0,
                "load_messages": False,
                "load_read_receipts": False,
                "before": None,
            }
            queries.append(_graphql.from_doc_id("2147762685294928", params))

        j = self.graphql_requests(*queries)

        for i, entry in enumerate(j):
            if entry.get("message_thread") is None:
                # If you don't have an existing thread with this person, attempt to retrieve user data anyways
                j[i]["message_thread"] = {
                    "thread_key": {"other_user_id": thread_ids[i]},
                    "thread_type": "ONE_TO_ONE",
                }

        pages_and_user_ids = [
            k["message_thread"]["thread_key"]["other_user_id"]
            for k in j
            if k["message_thread"].get("thread_type") == "ONE_TO_ONE"
        ]
        pages_and_users = {}
        if len(pages_and_user_ids) != 0:
            pages_and_users = self._fetchInfo(*pages_and_user_ids)

        rtn = {}
        for i, entry in enumerate(j):
            entry = entry["message_thread"]
            if entry.get("thread_type") == "GROUP":
                _id = entry["thread_key"]["thread_fbid"]
                rtn[_id] = Group._from_graphql(entry)
            elif entry.get("thread_type") == "ONE_TO_ONE":
                _id = entry["thread_key"]["other_user_id"]
                if pages_and_users.get(_id) is None:
                    raise FBchatException("Could not fetch thread {}".format(_id))
                entry.update(pages_and_users[_id])
                if entry["type"] == ThreadType.USER:
                    rtn[_id] = User._from_graphql(entry)
                else:
                    rtn[_id] = Page._from_graphql(entry)
            else:
                raise FBchatException(
                    "{} had an unknown thread type: {}".format(thread_ids[i], entry)
                )

        return rtn

    def fetchThreadMessages(self, thread_id=None, limit=20, before=None):
        """Fetch messages in a thread, ordered by most recent.

        Args:
            thread_id: User/Group ID to get messages from. See :ref:`intro_threads`
            limit (int): Max. number of messages to retrieve
            before (int): A timestamp, indicating from which point to retrieve messages

        Returns:
            list: :class:`Message` objects

        Raises:
            FBchatException: If request failed
        """
        thread_id, thread_type = self._getThread(thread_id, None)

        params = {
            "id": thread_id,
            "message_limit": limit,
            "load_messages": True,
            "load_read_receipts": True,
            "before": before,
        }
        (j,) = self.graphql_requests(_graphql.from_doc_id("1860982147341344", params))

        if j.get("message_thread") is None:
            raise FBchatException("Could not fetch thread {}: {}".format(thread_id, j))

        messages = [
            Message._from_graphql(message)
            for message in j["message_thread"]["messages"]["nodes"]
        ]
        messages.reverse()

        read_receipts = j["message_thread"]["read_receipts"]["nodes"]

        for message in messages:
            for receipt in read_receipts:
                if int(receipt["watermark"]) >= int(message.timestamp):
                    message.read_by.append(receipt["actor"]["id"])

        return messages

    def fetchThreadList(
        self, offset=None, limit=20, thread_location=ThreadLocation.INBOX, before=None
    ):
        """Fetch the client's thread list.

        Args:
            offset: Deprecated. Do not use!
            limit (int): Max. number of threads to retrieve. Capped at 20
            thread_location (ThreadLocation): INBOX, PENDING, ARCHIVED or OTHER
            before (int): A timestamp (in milliseconds), indicating from which point to retrieve threads

        Returns:
            list: :class:`Thread` objects

        Raises:
            FBchatException: If request failed
        """
        if offset is not None:
            log.warning(
                "Using `offset` in `fetchThreadList` is no longer supported, "
                "since Facebook migrated to the use of GraphQL in this request. "
                "Use `before` instead."
            )

        if limit > 20 or limit < 1:
            raise FBchatUserError("`limit` should be between 1 and 20")

        if thread_location in ThreadLocation:
            loc_str = thread_location.value
        else:
            raise FBchatUserError('"thread_location" must be a value of ThreadLocation')

        params = {
            "limit": limit,
            "tags": [loc_str],
            "before": before,
            "includeDeliveryReceipts": True,
            "includeSeqID": False,
        }
        (j,) = self.graphql_requests(_graphql.from_doc_id("1349387578499440", params))

        rtn = []
        for node in j["viewer"]["message_threads"]["nodes"]:
            _type = node.get("thread_type")
            if _type == "GROUP":
                rtn.append(Group._from_graphql(node))
            elif _type == "ONE_TO_ONE":
                rtn.append(User._from_thread_fetch(node))
            else:
                raise FBchatException(
                    "Unknown thread type: {}, with data: {}".format(_type, node)
                )
        return rtn

    def fetchUnread(self):
        """Fetch unread threads.

        Returns:
            list: List of unread thread ids

        Raises:
            FBchatException: If request failed
        """
        form = {
            "folders[0]": "inbox",
            "client": "mercury",
            "last_action_timestamp": now() - 60 * 1000
            # 'last_action_timestamp': 0
        }
        j = self._payload_post("/ajax/mercury/unread_threads.php", form)

        result = j["unread_thread_fbids"][0]
        return result["thread_fbids"] + result["other_user_fbids"]

    def fetchUnseen(self):
        """Fetch unseen / new threads.

        Returns:
            list: List of unseen thread ids

        Raises:
            FBchatException: If request failed
        """
        j = self._payload_post("/mercury/unseen_thread_ids/", {})

        result = j["unseen_thread_fbids"][0]
        return result["thread_fbids"] + result["other_user_fbids"]

    def fetchImageUrl(self, image_id):
        """Fetch URL to download the original image from an image attachment ID.

        Args:
            image_id (str): The image you want to fetch

        Returns:
            str: An URL where you can download the original image

        Raises:
            FBchatException: If request failed
        """
        image_id = str(image_id)
        data = {"photo_id": str(image_id)}
        j = self._post("/mercury/attachments/photo/", data)

        url = get_jsmods_require(j, 3)
        if url is None:
            raise FBchatException("Could not fetch image URL from: {}".format(j))
        return url

    def fetchMessageInfo(self, mid, thread_id=None):
        """Fetch `Message` object from the given message id.

        Args:
            mid: Message ID to fetch from
            thread_id: User/Group ID to get message info from. See :ref:`intro_threads`

        Returns:
            Message: :class:`Message` object

        Raises:
            FBchatException: If request failed
        """
        thread_id, thread_type = self._getThread(thread_id, None)
        message_info = self._forcedFetch(thread_id, mid).get("message")
        return Message._from_graphql(message_info)

    def fetchPollOptions(self, poll_id):
        """Fetch list of `PollOption` objects from the poll id.

        Args:
            poll_id: Poll ID to fetch from

        Returns:
            list

        Raises:
            FBchatException: If request failed
        """
        data = {"question_id": poll_id}
        j = self._payload_post("/ajax/mercury/get_poll_options", data)
        return [PollOption._from_graphql(m) for m in j]

    def fetchPlanInfo(self, plan_id):
        """Fetch `Plan` object from the plan id.

        Args:
            plan_id: Plan ID to fetch from

        Returns:
            Plan: :class:`Plan` object

        Raises:
            FBchatException: If request failed
        """
        data = {"event_reminder_id": plan_id}
        j = self._payload_post("/ajax/eventreminder", data)
        return Plan._from_fetch(j)

    def _getPrivateData(self):
        (j,) = self.graphql_requests(_graphql.from_doc_id("1868889766468115", {}))
        return j["viewer"]

    def getPhoneNumbers(self):
        """Fetch list of user's phone numbers.

        Returns:
            list: List of phone numbers
        """
        data = self._getPrivateData()
        return [
            j["phone_number"]["universal_number"] for j in data["user"]["all_phones"]
        ]

    def getEmails(self):
        """Fetch list of user's emails.

        Returns:
            list: List of emails
        """
        data = self._getPrivateData()
        return [j["display_email"] for j in data["all_emails"]]

    def getUserActiveStatus(self, user_id):
        """Fetch friend active status as an `ActiveStatus` object.

        Return ``None`` if status isn't known.

        Warning:
            Only works when listening.

        Args:
            user_id: ID of the user

        Returns:
            ActiveStatus: Given user active status
        """
        return self._buddylist.get(str(user_id))

    def fetchThreadImages(self, thread_id=None):
        """Fetch images posted in thread.

        Args:
            thread_id: ID of the thread

        Returns:
            typing.Iterable: :class:`ImageAttachment` or :class:`VideoAttachment`
        """
        thread_id, thread_type = self._getThread(thread_id, None)
        data = {"id": thread_id, "first": 48}
        thread_id = str(thread_id)
        (j,) = self.graphql_requests(_graphql.from_query_id("515216185516880", data))
        while True:
            try:
                i = j[thread_id]["message_shared_media"]["edges"][0]
            except IndexError:
                if j[thread_id]["message_shared_media"]["page_info"].get(
                    "has_next_page"
                ):
                    data["after"] = j[thread_id]["message_shared_media"][
                        "page_info"
                    ].get("end_cursor")
                    (j,) = self.graphql_requests(
                        _graphql.from_query_id("515216185516880", data)
                    )
                    continue
                else:
                    break

            if i["node"].get("__typename") == "MessageImage":
                yield ImageAttachment._from_list(i)
            elif i["node"].get("__typename") == "MessageVideo":
                yield VideoAttachment._from_list(i)
            else:
                yield Attachment(uid=i["node"].get("legacy_attachment_id"))
            del j[thread_id]["message_shared_media"]["edges"][0]

    """
    END FETCH METHODS
    """



    def _parseDelta(self, delta):
        def getThreadIdAndThreadType(msg_metadata):
            """Return a tuple consisting of thread ID and thread type."""
            id_thread = None
            type_thread = None
            if "threadFbId" in msg_metadata["threadKey"]:
                id_thread = str(msg_metadata["threadKey"]["threadFbId"])
                type_thread = ThreadType.GROUP
            elif "otherUserFbId" in msg_metadata["threadKey"]:
                id_thread = str(msg_metadata["threadKey"]["otherUserFbId"])
                type_thread = ThreadType.USER
            return id_thread, type_thread

        delta_type = delta.get("type")
        delta_class = delta.get("class")
        metadata = delta.get("messageMetadata")

        if metadata:
            mid = metadata["messageId"]
            author_id = str(metadata["actorFbId"])
            ts = int(metadata.get("timestamp"))

        # Added participants
        if "addedParticipants" in delta:
            added_ids = [str(x["userFbId"]) for x in delta["addedParticipants"]]
            thread_id = str(metadata["threadKey"]["threadFbId"])
            self.onPeopleAdded(
                mid=mid,
                added_ids=added_ids,
                author_id=author_id,
                thread_id=thread_id,
                ts=ts,
                msg=delta,
            )

        # Left/removed participants
        elif "leftParticipantFbId" in delta:
            removed_id = str(delta["leftParticipantFbId"])
            thread_id = str(metadata["threadKey"]["threadFbId"])
            self.onPersonRemoved(
                mid=mid,
                removed_id=removed_id,
                author_id=author_id,
                thread_id=thread_id,
                ts=ts,
                msg=delta,
            )

        # Color change
        elif delta_type == "change_thread_theme":
            new_color = ThreadColor._from_graphql(delta["untypedData"]["theme_color"])
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            self.onColorChange(
                mid=mid,
                author_id=author_id,
                new_color=new_color,
                thread_id=thread_id,
                thread_type=thread_type,
                ts=ts,
                metadata=metadata,
                msg=delta,
            )

        elif delta_class == "MarkFolderSeen":
            locations = [
                ThreadLocation(folder.lstrip("FOLDER_")) for folder in delta["folders"]
            ]
            self._onSeen(locations=locations, ts=delta["timestamp"], msg=delta)

        # Emoji change
        elif delta_type == "change_thread_icon":
            new_emoji = delta["untypedData"]["thread_icon"]
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            self.onEmojiChange(
                mid=mid,
                author_id=author_id,
                new_emoji=new_emoji,
                thread_id=thread_id,
                thread_type=thread_type,
                ts=ts,
                metadata=metadata,
                msg=delta,
            )

        # Thread title change
        elif delta_class == "ThreadName":
            new_title = delta["name"]
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            self.onTitleChange(
                mid=mid,
                author_id=author_id,
                new_title=new_title,
                thread_id=thread_id,
                thread_type=thread_type,
                ts=ts,
                metadata=metadata,
                msg=delta,
            )

        # Forced fetch
        elif delta_class == "ForcedFetch":
            mid = delta.get("messageId")
            if mid is None:
                if delta["threadKey"] is not None:
                    # Looks like the whole delta is metadata in this case
                    thread_id, thread_type = getThreadIdAndThreadType(delta)
                    self.onPendingMessage(
                        thread_id=thread_id,
                        thread_type=thread_type,
                        metadata=delta,
                        msg=delta,
                    )
                else:
                    self.onUnknownMesssageType(msg=delta)
            else:
                thread_id = str(delta["threadKey"]["threadFbId"])
                fetch_info = self._forcedFetch(thread_id, mid)
                fetch_data = fetch_info["message"]
                author_id = fetch_data["message_sender"]["id"]
                ts = fetch_data["timestamp_precise"]
                if fetch_data.get("__typename") == "ThreadImageMessage":
                    # Thread image change
                    image_metadata = fetch_data.get("image_with_metadata")
                    image_id = (
                        int(image_metadata["legacy_attachment_id"])
                        if image_metadata
                        else None
                    )
                    self.onImageChange(
                        mid=mid,
                        author_id=author_id,
                        new_image=image_id,
                        thread_id=thread_id,
                        thread_type=ThreadType.GROUP,
                        ts=ts,
                        msg=delta,
                    )

        # Nickname change
        elif delta_type == "change_thread_nickname":
            changed_for = str(delta["untypedData"]["participant_id"])
            new_nickname = delta["untypedData"]["nickname"]
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            self.onNicknameChange(
                mid=mid,
                author_id=author_id,
                changed_for=changed_for,
                new_nickname=new_nickname,
                thread_id=thread_id,
                thread_type=thread_type,
                ts=ts,
                metadata=metadata,
                msg=delta,
            )

        # Admin added or removed in a group thread
        elif delta_type == "change_thread_admins":
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            target_id = delta["untypedData"]["TARGET_ID"]
            admin_event = delta["untypedData"]["ADMIN_EVENT"]
            if admin_event == "add_admin":
                self.onAdminAdded(
                    mid=mid,
                    added_id=target_id,
                    author_id=author_id,
                    thread_id=thread_id,
                    thread_type=thread_type,
                    ts=ts,
                    msg=delta,
                )
            elif admin_event == "remove_admin":
                self.onAdminRemoved(
                    mid=mid,
                    removed_id=target_id,
                    author_id=author_id,
                    thread_id=thread_id,
                    thread_type=thread_type,
                    ts=ts,
                    msg=delta,
                )

        # Group approval mode change
        elif delta_type == "change_thread_approval_mode":
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            approval_mode = bool(int(delta["untypedData"]["APPROVAL_MODE"]))
            self.onApprovalModeChange(
                mid=mid,
                approval_mode=approval_mode,
                author_id=author_id,
                thread_id=thread_id,
                thread_type=thread_type,
                ts=ts,
                msg=delta,
            )

        # Message delivered
        elif delta_class == "DeliveryReceipt":
            message_ids = delta["messageIds"]
            delivered_for = str(
                delta.get("actorFbId") or delta["threadKey"]["otherUserFbId"]
            )
            ts = int(delta["deliveredWatermarkTimestampMs"])
            thread_id, thread_type = getThreadIdAndThreadType(delta)
            self.onMessageDelivered(
                msg_ids=message_ids,
                delivered_for=delivered_for,
                thread_id=thread_id,
                thread_type=thread_type,
                ts=ts,
                metadata=metadata,
                msg=delta,
            )

        # Message seen
        elif delta_class == "ReadReceipt":
            seen_by = str(delta.get("actorFbId") or delta["threadKey"]["otherUserFbId"])
            seen_ts = int(delta["actionTimestampMs"])
            delivered_ts = int(delta["watermarkTimestampMs"])
            thread_id, thread_type = getThreadIdAndThreadType(delta)
            self.onMessageSeen(
                seen_by=seen_by,
                thread_id=thread_id,
                thread_type=thread_type,
                seen_ts=seen_ts,
                ts=delivered_ts,
                metadata=metadata,
                msg=delta,
            )

        # Messages marked as seen
        elif delta_class == "MarkRead":
            seen_ts = int(
                delta.get("actionTimestampMs") or delta.get("actionTimestamp")
            )
            delivered_ts = int(
                delta.get("watermarkTimestampMs") or delta.get("watermarkTimestamp")
            )

            threads = []
            if "folders" not in delta:
                threads = [
                    getThreadIdAndThreadType({"threadKey": thr})
                    for thr in delta.get("threadKeys")
                ]

            # thread_id, thread_type = getThreadIdAndThreadType(delta)
            self.onMarkedSeen(
                threads=threads,
                seen_ts=seen_ts,
                ts=delivered_ts,
                metadata=delta,
                msg=delta,
            )

        # Game played
        elif delta_type == "instant_game_update":
            game_id = delta["untypedData"]["game_id"]
            game_name = delta["untypedData"]["game_name"]
            score = delta["untypedData"].get("score")
            if score is not None:
                score = int(score)
            leaderboard = delta["untypedData"].get("leaderboard")
            if leaderboard is not None:
                leaderboard = json.loads(leaderboard)["scores"]
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            self.onGamePlayed(
                mid=mid,
                author_id=author_id,
                game_id=game_id,
                game_name=game_name,
                score=score,
                leaderboard=leaderboard,
                thread_id=thread_id,
                thread_type=thread_type,
                ts=ts,
                metadata=metadata,
                msg=delta,
            )

        # Skip "no operation" events
        elif delta_class == "NoOp":
            pass

        # Group call started/ended
        elif delta_type == "rtc_call_log":
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            call_status = delta["untypedData"]["event"]
            call_duration = int(delta["untypedData"]["call_duration"])
            is_video_call = bool(int(delta["untypedData"]["is_video_call"]))
            if call_status == "call_started":
                self.onCallStarted(
                    mid=mid,
                    caller_id=author_id,
                    is_video_call=is_video_call,
                    thread_id=thread_id,
                    thread_type=thread_type,
                    ts=ts,
                    metadata=metadata,
                    msg=delta,
                )
            elif call_status == "call_ended":
                self.onCallEnded(
                    mid=mid,
                    caller_id=author_id,
                    is_video_call=is_video_call,
                    call_duration=call_duration,
                    thread_id=thread_id,
                    thread_type=thread_type,
                    ts=ts,
                    metadata=metadata,
                    msg=delta,
                )

        # User joined to group call
        elif delta_type == "participant_joined_group_call":
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            is_video_call = bool(int(delta["untypedData"]["group_call_type"]))
            self.onUserJoinedCall(
                mid=mid,
                joined_id=author_id,
                is_video_call=is_video_call,
                thread_id=thread_id,
                thread_type=thread_type,
                ts=ts,
                metadata=metadata,
                msg=delta,
            )

        # Group poll event
        elif delta_type == "group_poll":
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            event_type = delta["untypedData"]["event_type"]
            poll_json = json.loads(delta["untypedData"]["question_json"])
            poll = Poll._from_graphql(poll_json)
            if event_type == "question_creation":
                # User created group poll
                self.onPollCreated(
                    mid=mid,
                    poll=poll,
                    author_id=author_id,
                    thread_id=thread_id,
                    thread_type=thread_type,
                    ts=ts,
                    metadata=metadata,
                    msg=delta,
                )
            elif event_type == "update_vote":
                # User voted on group poll
                added_options = json.loads(delta["untypedData"]["added_option_ids"])
                removed_options = json.loads(delta["untypedData"]["removed_option_ids"])
                self.onPollVoted(
                    mid=mid,
                    poll=poll,
                    added_options=added_options,
                    removed_options=removed_options,
                    author_id=author_id,
                    thread_id=thread_id,
                    thread_type=thread_type,
                    ts=ts,
                    metadata=metadata,
                    msg=delta,
                )

        # Plan created
        elif delta_type == "lightweight_event_create":
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            self.onPlanCreated(
                mid=mid,
                plan=Plan._from_pull(delta["untypedData"]),
                author_id=author_id,
                thread_id=thread_id,
                thread_type=thread_type,
                ts=ts,
                metadata=metadata,
                msg=delta,
            )

        # Plan ended
        elif delta_type == "lightweight_event_notify":
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            self.onPlanEnded(
                mid=mid,
                plan=Plan._from_pull(delta["untypedData"]),
                thread_id=thread_id,
                thread_type=thread_type,
                ts=ts,
                metadata=metadata,
                msg=delta,
            )

        # Plan edited
        elif delta_type == "lightweight_event_update":
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            self.onPlanEdited(
                mid=mid,
                plan=Plan._from_pull(delta["untypedData"]),
                author_id=author_id,
                thread_id=thread_id,
                thread_type=thread_type,
                ts=ts,
                metadata=metadata,
                msg=delta,
            )

        # Plan deleted
        elif delta_type == "lightweight_event_delete":
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            self.onPlanDeleted(
                mid=mid,
                plan=Plan._from_pull(delta["untypedData"]),
                author_id=author_id,
                thread_id=thread_id,
                thread_type=thread_type,
                ts=ts,
                metadata=metadata,
                msg=delta,
            )

        # Plan participation change
        elif delta_type == "lightweight_event_rsvp":
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            take_part = delta["untypedData"]["guest_status"] == "GOING"
            self.onPlanParticipation(
                mid=mid,
                plan=Plan._from_pull(delta["untypedData"]),
                take_part=take_part,
                author_id=author_id,
                thread_id=thread_id,
                thread_type=thread_type,
                ts=ts,
                metadata=metadata,
                msg=delta,
            )

        # Client payload (that weird numbers)
        elif delta_class == "ClientPayload":
            payload = json.loads("".join(chr(z) for z in delta["payload"]))
            ts = now()  # Hack
            for d in payload.get("deltas", []):

                # Message reaction
                if d.get("deltaMessageReaction"):
                    i = d["deltaMessageReaction"]
                    thread_id, thread_type = getThreadIdAndThreadType(i)
                    mid = i["messageId"]
                    author_id = str(i["userId"])
                    
                    # Commented as the statement below invalidates other reaction emoji
                    
                    # reaction = (reaction = (
                    #     MessageReaction(i["reaction"]) if i.get("reaction") else None
                    # )
                    
                    add_reaction = not bool(i["action"])
                    if add_reaction:
                        self.onReactionAdded(
                            mid=mid,
                            reaction=i.get("reaction"),
                            author_id=author_id,
                            thread_id=thread_id,
                            thread_type=thread_type,
                            ts=ts,
                            msg=delta,
                        )
                    else:
                        self.onReactionRemoved(
                            mid=mid,
                            author_id=author_id,
                            thread_id=thread_id,
                            thread_type=thread_type,
                            ts=ts,
                            msg=delta,
                        )

                # Viewer status change
                elif d.get("deltaChangeViewerStatus"):
                    i = d["deltaChangeViewerStatus"]
                    thread_id, thread_type = getThreadIdAndThreadType(i)
                    author_id = str(i["actorFbid"])
                    reason = i["reason"]
                    can_reply = i["canViewerReply"]
                    if reason == 2:
                        if can_reply:
                            self.onUnblock(
                                author_id=author_id,
                                thread_id=thread_id,
                                thread_type=thread_type,
                                ts=ts,
                                msg=delta,
                            )
                        else:
                            self.onBlock(
                                author_id=author_id,
                                thread_id=thread_id,
                                thread_type=thread_type,
                                ts=ts,
                                msg=delta,
                            )

                # Live location info
                elif d.get("liveLocationData"):
                    i = d["liveLocationData"]
                    thread_id, thread_type = getThreadIdAndThreadType(i)
                    for l in i["messageLiveLocations"]:
                        mid = l["messageId"]
                        author_id = str(l["senderId"])
                        location = LiveLocationAttachment._from_pull(l)
                        self.onLiveLocation(
                            mid=mid,
                            location=location,
                            author_id=author_id,
                            thread_id=thread_id,
                            thread_type=thread_type,
                            ts=ts,
                            msg=delta,
                        )

                # Message deletion
                elif d.get("deltaRecallMessageData"):
                    i = d["deltaRecallMessageData"]
                    thread_id, thread_type = getThreadIdAndThreadType(i)
                    mid = i["messageID"]
                    ts = i["deletionTimestamp"]
                    author_id = str(i["senderID"])
                    self.onMessageUnsent(
                        mid=mid,
                        author_id=author_id,
                        thread_id=thread_id,
                        thread_type=thread_type,
                        ts=ts,
                        msg=delta,
                    )

                elif d.get("deltaMessageReply"):
                    i = d["deltaMessageReply"]
                    metadata = i["message"]["messageMetadata"]
                    thread_id, thread_type = getThreadIdAndThreadType(metadata)
                    message = Message._from_reply(i["message"])
                    message.replied_to = Message._from_reply(i["repliedToMessage"])
                    message.reply_to_id = message.replied_to.uid
                    self.onMessage(
                        mid=message.uid,
                        author_id=message.author,
                        message=message.text,
                        message_object=message,
                        thread_id=thread_id,
                        thread_type=thread_type,
                        ts=message.timestamp,
                        metadata=metadata,
                        msg=delta,
                    )

        # New message
        elif delta.get("class") == "NewMessage":
            thread_id, thread_type = getThreadIdAndThreadType(metadata)
            self.onMessage(
                mid=mid,
                author_id=author_id,
                message=delta.get("body", ""),
                message_object=Message._from_pull(
                    delta,
                    mid=mid,
                    tags=metadata.get("tags"),
                    author=author_id,
                    timestamp=ts,
                ),
                thread_id=thread_id,
                thread_type=thread_type,
                ts=ts,
                metadata=metadata,
                msg=delta,
            )

        # New pending message
        elif delta_class == "ThreadFolder" and delta.get("folder") == "FOLDER_PENDING":
            # Looks like the whole delta is metadata in this case
            thread_id, thread_type = getThreadIdAndThreadType(delta)
            self.onPendingMessage(
                thread_id=thread_id, thread_type=thread_type, metadata=delta, msg=delta
            )

        # Unknown message type
        else:
            self.onUnknownMesssageType(msg=delta)

    def _parse_payload(self, topic, m):
        # Things that directly change chat
        if "/t_ms" in topic:
            if "deltas" not in m:
                return
            for delta in m["deltas"]:
                self._parseDelta(delta)

        # TODO: Remove old parsing below

        # Inbox
        elif "inbox" in topic:
            self.onInbox(
                unseen=m["unseen"],
                unread=m["unread"],
                recent_unread=m["recent_unread"],
                msg=m,
            )

        # Typing
        # /thread_typing {'sender_fbid': X, 'state': 1, 'type': 'typ', 'thread': 'Y'}
        # /orca_typing_notifications {'type': 'typ', 'sender_fbid': X, 'state': 0}
        elif topic in ("/thread_typing", "/orca_typing_notifications"):
            author_id = str(m["sender_fbid"])
            thread_id = m.get("thread", author_id)
            typing_status = TypingStatus(m.get("state"))
            thread_type = (
                ThreadType.USER if thread_id == author_id else ThreadType.GROUP
            )
            self.onTyping(
                author_id=author_id,
                status=typing_status,
                thread_id=thread_id,
                thread_type=thread_type,
                msg=m,
            )

        # Other notifications
        elif "/legacy_web" in topic:
            # Friend request
            if m["type"] == "jewel_requests_add":
                from_id = m["from"]
                # TODO: from_id = str(from_id)
                self.onFriendRequest(from_id=from_id, msg=m)
            else:
                self.onUnknownMesssageType(msg=m)

        # Chat timestamp / Buddylist overlay
        elif "/orca_presence" in topic:
            if m["list_type"] == "full":
                self._buddylist = {}  # Refresh internal list

            statuses = dict()
            for data in m["list"]:
                user_id = str(data["u"])
                statuses[user_id] = ActiveStatus._from_orca_presence(data)
                self._buddylist[user_id] = statuses[user_id]

            # TODO: Which one should we call?
            self.onChatTimestamp(buddylist=statuses, msg=m)
            self.onBuddylistOverlay(statuses=statuses, msg=m)

        # Unknown message type
        else:
            self.onUnknownMesssageType(msg=m)

    def _parse_message(self, topic, data):
        try:
            self._parse_payload(topic, data)
        except Exception as e:
            self.onMessageError(exception=e, msg=data)

    def startListening(self):
        """Start listening from an external event loop.

        Raises:
            FBchatException: If request failed
        """
        if not self._mqtt:
            self._mqtt = Mqtt.connect(
                state=self._state,
                on_message=self._parse_message,
                chat_on=self._markAlive,
                foreground=True,
            )
            # Backwards compat
            self.onQprimer(ts=now(), msg=None)
        self.listening = True

    def doOneListen(self, markAlive=None):
        """Do one cycle of the listening loop.

        This method is useful if you want to control the client from an external event
        loop.

        Warning:
            ``markAlive`` parameter is deprecated, use :func:`Client.setActiveStatus`
            or ``markAlive`` parameter in :func:`Client.listen` instead.

        Returns:
            bool: Whether the loop should keep running
        """
        if markAlive is not None:
            self._markAlive = markAlive

        # TODO: Remove this wierd check, and let the user handle the chat_on parameter
        if self._markAlive != self._mqtt._chat_on:
            self._mqtt.set_chat_on(self._markAlive)

        # TODO: Remove on_error param
        return self._mqtt.loop_once(on_error=lambda e: self.onListenError(exception=e))

    def stopListening(self):
        """Stop the listening loop."""
        self.listening = False
        if not self._mqtt:
            return
        self._mqtt.disconnect()
        # TODO: Preserve the _mqtt object
        # Currently, there's some issues when disconnecting
        self._mqtt = None

    def listen(self, markAlive=None):
        """Initialize and runs the listening loop continually.

        Args:
            markAlive (bool): Whether this should ping the Facebook server each time the loop runs
        """
        if markAlive is not None:
            self.setActiveStatus(markAlive)

        self.startListening()
        self.onListening()

        while self.listening and self.doOneListen():
            pass

        self.stopListening()

    def setActiveStatus(self, markAlive):
        """Change active status while listening.

        Args:
            markAlive (bool): Whether to show if client is active
        """
        self._markAlive = markAlive

    """
    END LISTEN METHODS
    """

    """
    EVENTS
    """

    def onLoggingIn(self, email=None):
        """Called when the client is logging in.

        Args:
            email: The email of the client
        """
        log.info("Logging in {}...".format(email))

    def on2FACode(self):
        """Called when a 2FA code is needed to progress."""
        return input("Please enter your 2FA code --> ")

    def onLoggedIn(self, email=None):
        """Called when the client is successfully logged in.

        Args:
            email: The email of the client
        """
        log.info("Login of {} successful.".format(email))

    def onListening(self):
        """Called when the client is listening."""
        log.info("Listening...")

    def onListenError(self, exception=None):
        """Called when an error was encountered while listening.

        Args:
            exception: The exception that was encountered

        Returns:
            Whether the loop should keep running
        """
        log.exception("Got exception while listening")
        return True

    def onMessage(
        self,
        mid=None,
        author_id=None,
        message=None,
        message_object=None,
        thread_id=None,
        thread_type=ThreadType.USER,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody sends a message.

        Args:
            mid: The message ID
            author_id: The ID of the author
            message: (deprecated. Use ``message_object.text`` instead)
            message_object (Message): The message (As a `Message` object)
            thread_id: Thread ID that the message was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the message was sent to. See :ref:`intro_threads`
            ts: The timestamp of the message
            metadata: Extra metadata about the message
            msg: A full set of the data received
        """
        # print(self.page)
        log.info("{} from {} in {}".format(message_object, thread_id, thread_type.name))

    def onPendingMessage(
        self, thread_id=None, thread_type=None, metadata=None, msg=None
    ):
        """Called when the client is listening, and somebody that isn't
         connected with you on either Facebook or Messenger sends a message.
         After that, you need to use fetchThreadList to actually read the message.

         Args:
            thread_id: Thread ID that the message was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the message was sent to. See :ref:`intro_threads`
            metadata: Extra metadata about the message
            msg: A full set of the data received
        """
        log.info("New pending message from {}".format(thread_id))

    def onColorChange(
        self,
        mid=None,
        author_id=None,
        new_color=None,
        thread_id=None,
        thread_type=ThreadType.USER,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody changes a thread's color.

        Args:
            mid: The action ID
            author_id: The ID of the person who changed the color
            new_color (ThreadColor): The new color
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "Color change from {} in {} ({}): {}".format(
                author_id, thread_id, thread_type.name, new_color
            )
        )

    def onEmojiChange(
        self,
        mid=None,
        author_id=None,
        new_emoji=None,
        thread_id=None,
        thread_type=ThreadType.USER,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody changes a thread's emoji.

        Args:
            mid: The action ID
            author_id: The ID of the person who changed the emoji
            new_emoji: The new emoji
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "Emoji change from {} in {} ({}): {}".format(
                author_id, thread_id, thread_type.name, new_emoji
            )
        )

    def onTitleChange(
        self,
        mid=None,
        author_id=None,
        new_title=None,
        thread_id=None,
        thread_type=ThreadType.USER,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody changes a thread's title.

        Args:
            mid: The action ID
            author_id: The ID of the person who changed the title
            new_title: The new title
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "Title change from {} in {} ({}): {}".format(
                author_id, thread_id, thread_type.name, new_title
            )
        )

    def onImageChange(
        self,
        mid=None,
        author_id=None,
        new_image=None,
        thread_id=None,
        thread_type=ThreadType.GROUP,
        ts=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody changes a thread's image.

        Args:
            mid: The action ID
            author_id: The ID of the person who changed the image
            new_image: The ID of the new image
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            msg: A full set of the data received
        """
        log.info("{} changed thread image in {}".format(author_id, thread_id))

    def onNicknameChange(
        self,
        mid=None,
        author_id=None,
        changed_for=None,
        new_nickname=None,
        thread_id=None,
        thread_type=ThreadType.USER,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody changes a nickname.

        Args:
            mid: The action ID
            author_id: The ID of the person who changed the nickname
            changed_for: The ID of the person whom got their nickname changed
            new_nickname: The new nickname
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "Nickname change from {} in {} ({}) for {}: {}".format(
                author_id, thread_id, thread_type.name, changed_for, new_nickname
            )
        )

    def onAdminAdded(
        self,
        mid=None,
        added_id=None,
        author_id=None,
        thread_id=None,
        thread_type=ThreadType.GROUP,
        ts=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody adds an admin to a group.

        Args:
            mid: The action ID
            added_id: The ID of the admin who got added
            author_id: The ID of the person who added the admins
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            msg: A full set of the data received
        """
        log.info("{} added admin: {} in {}".format(author_id, added_id, thread_id))

    def onAdminRemoved(
        self,
        mid=None,
        removed_id=None,
        author_id=None,
        thread_id=None,
        thread_type=ThreadType.GROUP,
        ts=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody is removed as an admin in a group.

        Args:
            mid: The action ID
            removed_id: The ID of the admin who got removed
            author_id: The ID of the person who removed the admins
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            msg: A full set of the data received
        """
        log.info("{} removed admin: {} in {}".format(author_id, removed_id, thread_id))

    def onApprovalModeChange(
        self,
        mid=None,
        approval_mode=None,
        author_id=None,
        thread_id=None,
        thread_type=ThreadType.GROUP,
        ts=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody changes approval mode in a group.

        Args:
            mid: The action ID
            approval_mode: True if approval mode is activated
            author_id: The ID of the person who changed approval mode
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            msg: A full set of the data received
        """
        if approval_mode:
            log.info("{} activated approval mode in {}".format(author_id, thread_id))
        else:
            log.info("{} disabled approval mode in {}".format(author_id, thread_id))

    def onMessageSeen(
        self,
        seen_by=None,
        thread_id=None,
        thread_type=ThreadType.USER,
        seen_ts=None,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody marks a message as seen.

        Args:
            seen_by: The ID of the person who marked the message as seen
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            seen_ts: A timestamp of when the person saw the message
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "Messages seen by {} in {} ({}) at {}s".format(
                seen_by, thread_id, thread_type.name, seen_ts / 1000
            )
        )

    def onMessageDelivered(
        self,
        msg_ids=None,
        delivered_for=None,
        thread_id=None,
        thread_type=ThreadType.USER,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody marks messages as delivered.

        Args:
            msg_ids: The messages that are marked as delivered
            delivered_for: The person that marked the messages as delivered
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "Messages {} delivered to {} in {} ({}) at {}s".format(
                msg_ids, delivered_for, thread_id, thread_type.name, ts / 1000
            )
        )

    def onMarkedSeen(
        self, threads=None, seen_ts=None, ts=None, metadata=None, msg=None
    ):
        """Called when the client is listening, and the client has successfully marked threads as seen.

        Args:
            threads: The threads that were marked
            author_id: The ID of the person who changed the emoji
            seen_ts: A timestamp of when the threads were seen
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "Marked messages as seen in threads {} at {}s".format(
                [(x[0], x[1].name) for x in threads], seen_ts / 1000
            )
        )

    def onMessageUnsent(
        self,
        mid=None,
        author_id=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        msg=None,
    ):
        """Called when the client is listening, and someone unsends (deletes for everyone) a message.

        Args:
            mid: ID of the unsent message
            author_id: The ID of the person who unsent the message
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            msg: A full set of the data received
        """
        log.info(
            "{} unsent the message {} in {} ({}) at {}s".format(
                author_id, repr(mid), thread_id, thread_type.name, ts / 1000
            )
        )

    def onPeopleAdded(
        self,
        mid=None,
        added_ids=None,
        author_id=None,
        thread_id=None,
        ts=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody adds people to a group thread.

        Args:
            mid: The action ID
            added_ids: The IDs of the people who got added
            author_id: The ID of the person who added the people
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            msg: A full set of the data received
        """
        log.info(
            "{} added: {} in {}".format(author_id, ", ".join(added_ids), thread_id)
        )

    def onPersonRemoved(
        self,
        mid=None,
        removed_id=None,
        author_id=None,
        thread_id=None,
        ts=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody removes a person from a group thread.

        Args:
            mid: The action ID
            removed_id: The ID of the person who got removed
            author_id: The ID of the person who removed the person
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            msg: A full set of the data received
        """
        log.info("{} removed: {} in {}".format(author_id, removed_id, thread_id))

    def onFriendRequest(self, from_id=None, msg=None):
        """Called when the client is listening, and somebody sends a friend request.

        Args:
            from_id: The ID of the person that sent the request
            msg: A full set of the data received
        """
        log.info("Friend request from {}".format(from_id))

    def _onSeen(self, locations=None, ts=None, msg=None):
        """
        Todo:
            Document this, and make it public

        Args:
            locations: ---
            ts: A timestamp of the action
            msg: A full set of the data received
        """

    def onInbox(self, unseen=None, unread=None, recent_unread=None, msg=None):
        """
        Todo:
            Documenting this

        Args:
            unseen: --
            unread: --
            recent_unread: --
            msg: A full set of the data received
        """
        log.info("Inbox event: {}, {}, {}".format(unseen, unread, recent_unread))

    def onTyping(
        self, author_id=None, status=None, thread_id=None, thread_type=None, msg=None
    ):
        """Called when the client is listening, and somebody starts or stops typing into a chat.

        Args:
            author_id: The ID of the person who sent the action
            status (TypingStatus): The typing status
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            msg: A full set of the data received
        """
        pass

    def onGamePlayed(
        self,
        mid=None,
        author_id=None,
        game_id=None,
        game_name=None,
        score=None,
        leaderboard=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody plays a game.

        Args:
            mid: The action ID
            author_id: The ID of the person who played the game
            game_id: The ID of the game
            game_name: Name of the game
            score: Score obtained in the game
            leaderboard: Actual leader board of the game in the thread
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            '{} played "{}" in {} ({})'.format(
                author_id, game_name, thread_id, thread_type.name
            )
        )

    def onReactionAdded(
        self,
        mid=None,
        reaction=None,
        author_id=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody reacts to a message.

        Args:
            mid: Message ID, that user reacted to
            reaction (MessageReaction): Reaction
            author_id: The ID of the person who reacted to the message
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            msg: A full set of the data received
        """
        log.info(
            "{} reacted to message {} with {} in {} ({})".format(
                author_id, mid, reaction.name, thread_id, thread_type.name
            )
        )

    def onReactionRemoved(
        self,
        mid=None,
        author_id=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody removes reaction from a message.

        Args:
            mid: Message ID, that user reacted to
            author_id: The ID of the person who removed reaction
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            msg: A full set of the data received
        """
        log.info(
            "{} removed reaction from {} message in {} ({})".format(
                author_id, mid, thread_id, thread_type
            )
        )

    def onBlock(
        self, author_id=None, thread_id=None, thread_type=None, ts=None, msg=None
    ):
        """Called when the client is listening, and somebody blocks client.

        Args:
            author_id: The ID of the person who blocked
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            msg: A full set of the data received
        """
        log.info(
            "{} blocked {} ({}) thread".format(author_id, thread_id, thread_type.name)
        )

    def onUnblock(
        self, author_id=None, thread_id=None, thread_type=None, ts=None, msg=None
    ):
        """Called when the client is listening, and somebody blocks client.

        Args:
            author_id: The ID of the person who unblocked
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            msg: A full set of the data received
        """
        log.info(
            "{} unblocked {} ({}) thread".format(author_id, thread_id, thread_type.name)
        )

    def onLiveLocation(
        self,
        mid=None,
        location=None,
        author_id=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        msg=None,
    ):
        """Called when the client is listening and somebody sends live location info.

        Args:
            mid: The action ID
            location (LiveLocationAttachment): Sent location info
            author_id: The ID of the person who sent location info
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            msg: A full set of the data received
        """
        log.info(
            "{} sent live location info in {} ({}) with latitude {} and longitude {}".format(
                author_id, thread_id, thread_type, location.latitude, location.longitude
            )
        )

    def onCallStarted(
        self,
        mid=None,
        caller_id=None,
        is_video_call=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody starts a call in a group.

        Todo:
            Make this work with private calls.

        Args:
            mid: The action ID
            caller_id: The ID of the person who started the call
            is_video_call: True if it's video call
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "{} started call in {} ({})".format(caller_id, thread_id, thread_type.name)
        )

    def onCallEnded(
        self,
        mid=None,
        caller_id=None,
        is_video_call=None,
        call_duration=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody ends a call in a group.

        Todo:
            Make this work with private calls.

        Args:
            mid: The action ID
            caller_id: The ID of the person who ended the call
            is_video_call: True if it was video call
            call_duration: Call duration in seconds
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "{} ended call in {} ({})".format(caller_id, thread_id, thread_type.name)
        )

    def onUserJoinedCall(
        self,
        mid=None,
        joined_id=None,
        is_video_call=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody joins a group call.

        Args:
            mid: The action ID
            joined_id: The ID of the person who joined the call
            is_video_call: True if it's video call
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "{} joined call in {} ({})".format(joined_id, thread_id, thread_type.name)
        )

    def onPollCreated(
        self,
        mid=None,
        poll=None,
        author_id=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody creates a group poll.

        Args:
            mid: The action ID
            poll (Poll): Created poll
            author_id: The ID of the person who created the poll
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "{} created poll {} in {} ({})".format(
                author_id, poll, thread_id, thread_type.name
            )
        )

    def onPollVoted(
        self,
        mid=None,
        poll=None,
        added_options=None,
        removed_options=None,
        author_id=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody votes in a group poll.

        Args:
            mid: The action ID
            poll (Poll): Poll, that user voted in
            author_id: The ID of the person who voted in the poll
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "{} voted in poll {} in {} ({})".format(
                author_id, poll, thread_id, thread_type.name
            )
        )

    def onPlanCreated(
        self,
        mid=None,
        plan=None,
        author_id=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody creates a plan.

        Args:
            mid: The action ID
            plan (Plan): Created plan
            author_id: The ID of the person who created the plan
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "{} created plan {} in {} ({})".format(
                author_id, plan, thread_id, thread_type.name
            )
        )

    def onPlanEnded(
        self,
        mid=None,
        plan=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and a plan ends.

        Args:
            mid: The action ID
            plan (Plan): Ended plan
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "Plan {} has ended in {} ({})".format(plan, thread_id, thread_type.name)
        )

    def onPlanEdited(
        self,
        mid=None,
        plan=None,
        author_id=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody edits a plan.

        Args:
            mid: The action ID
            plan (Plan): Edited plan
            author_id: The ID of the person who edited the plan
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "{} edited plan {} in {} ({})".format(
                author_id, plan, thread_id, thread_type.name
            )
        )

    def onPlanDeleted(
        self,
        mid=None,
        plan=None,
        author_id=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody deletes a plan.

        Args:
            mid: The action ID
            plan (Plan): Deleted plan
            author_id: The ID of the person who deleted the plan
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        log.info(
            "{} deleted plan {} in {} ({})".format(
                author_id, plan, thread_id, thread_type.name
            )
        )

    def onPlanParticipation(
        self,
        mid=None,
        plan=None,
        take_part=None,
        author_id=None,
        thread_id=None,
        thread_type=None,
        ts=None,
        metadata=None,
        msg=None,
    ):
        """Called when the client is listening, and somebody takes part in a plan or not.

        Args:
            mid: The action ID
            plan (Plan): Plan
            take_part (bool): Whether the person takes part in the plan or not
            author_id: The ID of the person who will participate in the plan or not
            thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
            thread_type (ThreadType): Type of thread that the action was sent to. See :ref:`intro_threads`
            ts: A timestamp of the action
            metadata: Extra metadata about the action
            msg: A full set of the data received
        """
        if take_part:
            log.info(
                "{} will take part in {} in {} ({})".format(
                    author_id, plan, thread_id, thread_type.name
                )
            )
        else:
            log.info(
                "{} won't take part in {} in {} ({})".format(
                    author_id, plan, thread_id, thread_type.name
                )
            )

    def onQprimer(self, ts=None, msg=None):
        """Called when the client just started listening.

        Args:
            ts: A timestamp of the action
            msg: A full set of the data received
        """
        pass

    def onChatTimestamp(self, buddylist=None, msg=None):
        """Called when the client receives chat online presence update.

        Args:
            buddylist: A list of dictionaries with friend id and last seen timestamp
            msg: A full set of the data received
        """
        log.debug("Chat Timestamps received: {}".format(buddylist))

    def onBuddylistOverlay(self, statuses=None, msg=None):
        """Called when the client is listening and client receives information about friend active status.

        Args:
            statuses (dict): Dictionary with user IDs as keys and :class:`ActiveStatus` as values
            msg: A full set of the data received
        """

    def onUnknownMesssageType(self, msg=None):
        """Called when the client is listening, and some unknown data was received.

        Args:
            msg: A full set of the data received
        """
        # log.debug("Unknown message received: {}".format(msg))
        pass

    def onMessageError(self, exception=None, msg=None):
        """Called when an error was encountered while parsing received data.

        Args:
            exception: The exception that was encountered
            msg: A full set of the data received
        """
        log.exception("Exception in parsing of {}".format(msg))

    
    ### asyncio MADNESS
    
    def parseFromBinary(self, response):
        response = str(response)
        first_index = 0 # Index for '/'
        second_index = 0 # Index for '{'
        
        allowed_chars = string.ascii_lowercase + "-_"

        for i, char in enumerate(response):
            # print(i, char)
            if char == '/':
                first_index = i
            elif char == "{":
                second_index = i
                break
        topic = response[first_index:second_index]
        data = response[second_index:len(response)]

        # print("topic", topic)
        # print("data", data)

        try:
            data = json.loads(data)
            self._parse_message(topic, data)
        except JSONDecodeError:
            pass
        except:
            print(traceback.format_exc())

    def printResponse(self, response):
        data = response["response"]["payloadData"]
        # print("\n")
        # print(data)
        temp = ''
        try:
            # print("\n")
            data = base64.b64decode(data)
            # data = str(data)
            # print(data[0])
            # print(data)
            # print('\n')
            for char in data:
                tt = chr(char)
                if tt.isprintable():
                    temp += tt
            
            # print(temp)
            self.parseFromBinary(temp)
            
        except Exception:
            print(traceback.format_exc())
            
            
        # print(decoded.decode())
        # parseFromBinary(decoded)
        # print(response)


    async def main(self):
        self.browser = await launch(
            headless=False,
            args=['--no-sandbox'],
            autoClose=False,
            executablePath='/usr/bin/chromium',
            userDataDir="userdata"
        )
        # self.page = await browser.newPage()
        
        self.tabs = await self.browser.pages()
        await self.tabs[0].goto('https://m.facebook.com/messages')
        cdp = await self.tabs[0].target.createCDPSession()
        await cdp.send('Network.enable')
        await cdp.send('Page.enable')


        cdp.on('Network.webSocketFrameReceived', self.printResponse)  # Calls printResponse when a websocket is received
        cdp.on('Network.webSocketFrameSent', self.printResponse)  # Calls printResponse when a websocket is sent
        await asyncio.sleep(1000)
    
    def __init__(
        self
    ):
        print("gg")
        
        asyncio.get_event_loop().run_until_complete(self.main())
       