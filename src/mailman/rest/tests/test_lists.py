# Copyright (C) 2011-2016 by the Free Software Foundation, Inc.
#
# This file is part of GNU Mailman.
#
# GNU Mailman is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# GNU Mailman is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along with
# GNU Mailman.  If not, see <http://www.gnu.org/licenses/>.

"""REST list tests."""

import unittest

from datetime import timedelta
from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.database.transaction import transaction
from mailman.interfaces.digests import DigestFrequency
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.mailinglist import IAcceptableAliasSet
from mailman.interfaces.member import DeliveryMode
from mailman.interfaces.usermanager import IUserManager
from mailman.model.mailinglist import AcceptableAlias
from mailman.runners.digest import DigestRunner
from mailman.testing.helpers import (
    call_api, get_queue_messages, make_testable_runner,
    specialized_message_from_string as mfs)
from mailman.testing.layers import RESTLayer
from mailman.utilities.datetime import now as right_now
from urllib.error import HTTPError
from zope.component import getUtility


class TestListsMissing(unittest.TestCase):
    """Test expected failures."""

    layer = RESTLayer

    def test_missing_list_roster_member_404(self):
        # /lists/<missing>/roster/member gives 404
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/missing@example.com'
                     '/roster/member')
        self.assertEqual(cm.exception.code, 404)

    def test_missing_list_roster_owner_404(self):
        # /lists/<missing>/roster/owner gives 404
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/missing@example.com'
                     '/roster/owner')
        self.assertEqual(cm.exception.code, 404)

    def test_missing_list_roster_moderator_404(self):
        # /lists/<missing>/roster/member gives 404
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/missing@example.com'
                     '/roster/moderator')
        self.assertEqual(cm.exception.code, 404)

    def test_missing_list_configuration_404(self):
        # /lists/<missing>/config gives 404
        with self.assertRaises(HTTPError) as cm:
            call_api(
                'http://localhost:9001/3.0/lists/missing@example.com/config')
        self.assertEqual(cm.exception.code, 404)


class TestLists(unittest.TestCase):
    """Test various aspects of mailing list resources."""

    layer = RESTLayer

    def setUp(self):
        with transaction():
            self._mlist = create_list('test@example.com')
        self._usermanager = getUtility(IUserManager)

    def test_member_count_with_no_members(self):
        # The list initially has 0 members.
        resource, response = call_api(
            'http://localhost:9001/3.0/lists/test@example.com')
        self.assertEqual(response.status, 200)
        self.assertEqual(resource['member_count'], 0)

    def test_member_count_with_one_member(self):
        # Add a member to a list and check that the resource reflects this.
        with transaction():
            anne = self._usermanager.create_address('anne@example.com')
            self._mlist.subscribe(anne)
        resource, response = call_api(
            'http://localhost:9001/3.0/lists/test@example.com')
        self.assertEqual(response.status, 200)
        self.assertEqual(resource['member_count'], 1)

    def test_member_count_with_two_members(self):
        # Add two members to a list and check that the resource reflects this.
        with transaction():
            anne = self._usermanager.create_address('anne@example.com')
            self._mlist.subscribe(anne)
            bart = self._usermanager.create_address('bar@example.com')
            self._mlist.subscribe(bart)
        resource, response = call_api(
            'http://localhost:9001/3.0/lists/test@example.com')
        self.assertEqual(response.status, 200)
        self.assertEqual(resource['member_count'], 2)

    def test_query_for_lists_in_missing_domain(self):
        # You cannot ask all the mailing lists in a non-existent domain.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/domains/no.example.org/lists')
        self.assertEqual(cm.exception.code, 404)

    def test_cannot_create_list_in_missing_domain(self):
        # You cannot create a mailing list in a domain that does not exist.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists', {
                     'fqdn_listname': 'ant@no-domain.example.org',
                     })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason,
                         b'Domain does not exist: no-domain.example.org')

    def test_cannot_create_duplicate_list(self):
        # You cannot create a list that already exists.
        call_api('http://localhost:9001/3.0/lists', {
                 'fqdn_listname': 'ant@example.com',
                 })
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists', {
                     'fqdn_listname': 'ant@example.com',
                     })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason, b'Mailing list exists')

    def test_cannot_delete_missing_list(self):
        # You cannot delete a list that does not exist.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/bee.example.com',
                     method='DELETE')
        self.assertEqual(cm.exception.code, 404)

    def test_cannot_delete_already_deleted_list(self):
        # You cannot delete a list twice.
        call_api('http://localhost:9001/3.0/lists', {
                 'fqdn_listname': 'ant@example.com',
                 })
        call_api('http://localhost:9001/3.0/lists/ant.example.com',
                 method='DELETE')
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant.example.com',
                     method='DELETE')
        self.assertEqual(cm.exception.code, 404)

    def test_roster(self):
        # Lists have rosters which can be accessed by role.
        with transaction():
            anne = self._usermanager.create_address('anne@example.com')
            bart = self._usermanager.create_address('bart@example.com')
            self._mlist.subscribe(anne)
            self._mlist.subscribe(bart)
        resource, response = call_api(
            'http://localhost:9001/3.0/lists/test@example.com/roster/member')
        self.assertEqual(resource['start'], 0)
        self.assertEqual(resource['total_size'], 2)
        member = resource['entries'][0]
        self.assertEqual(member['email'], 'anne@example.com')
        self.assertEqual(member['role'], 'member')
        member = resource['entries'][1]
        self.assertEqual(member['email'], 'bart@example.com')
        self.assertEqual(member['role'], 'member')

    def test_delete_list_with_acceptable_aliases(self):
        # LP: #1432239 - deleting a mailing list with acceptable aliases
        # causes a SQLAlchemy error.  The aliases must be deleted first.
        with transaction():
            alias_set = IAcceptableAliasSet(self._mlist)
            alias_set.add('bee@example.com')
        call_api('http://localhost:9001/3.0/lists/test.example.com',
                 method='DELETE')
        # Neither the mailing list, nor the aliases are present.
        self.assertIsNone(getUtility(IListManager).get('test@example.com'))
        self.assertEqual(config.db.store.query(AcceptableAlias).count(), 0)

    def test_bad_roster_matcher(self):
        # Try to get a list's roster, but the roster name is bogus.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant.example.com'
                     '/roster/bogus')
        self.assertEqual(cm.exception.code, 404)

    def test_bad_config_matcher(self):
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant.example.com'
                     '/config/volume/bogus')
        self.assertEqual(cm.exception.code, 404)

    def test_bad_list_get(self):
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/bogus.example.com')
        self.assertEqual(cm.exception.code, 404)

    def test_not_found_member_role(self):
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/test.example.com'
                     '/owner/nobody@example.com')
        self.assertEqual(cm.exception.code, 404)

    def test_list_mass_unsubscribe(self):
        with transaction():
            aperson = self._usermanager.create_address('aperson@test.com')
            bperson = self._usermanager.create_address('bperson@test.com')
            cperson = self._usermanager.create_address('cperson@test.com')
            mlist = create_list('testlist@example.com')
            mlist.subscribe(aperson)
            mlist.subscribe(bperson)
            mlist.subscribe(cperson)
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/bogus.example.com'
                     '/roster/member', None, 'DELETE')
        self.assertEqual(cm.exception.code, 404)
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/testlist.example.com'
                     '/roster/member', None, 'DELETE')
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason, b'Invalid Input.')
        resource, response = call_api(
            'http://127.0.0.1:9001/3.0/lists/testlist.example.com'
            '/roster/member', {'emails': ['aperson@test.com']}, 'DELETE')
        self.assertEqual(response.status, 204)
        resource, response = call_api(
            'http://127.0.0.1:9001/3.0/lists/testlist.example.com'
            '/roster/member', {'emails': ['bperson@test.com',
                                          'cperson@test.com',
                                          'bperson@test.com',
                                          'bogus@test.com']}, 'DELETE')
        self.assertEqual(response.status, 200)
        self.assertEqual(resource['bperson@test.com'],
                         'Member already deleted.')
        self.assertEqual(resource['bogus@test.com'], 'No such member.')


class TestListArchivers(unittest.TestCase):
    """Test corner cases for list archivers."""

    layer = RESTLayer

    def setUp(self):
        with transaction():
            self._mlist = create_list('ant@example.com')

    def test_archiver_statuses(self):
        resource, response = call_api(
            'http://localhost:9001/3.0/lists/ant.example.com/archivers')
        self.assertEqual(response.status, 200)
        # Remove the variable data.
        resource.pop('http_etag')
        self.assertEqual(resource, {
            'mail-archive': True,
            'mhonarc': True,
            'prototype': True,
            })

    def test_archiver_statuses_on_missing_lists(self):
        # You cannot get the archiver statuses on a list that doesn't exist.
        with self.assertRaises(HTTPError) as cm:
            call_api(
                'http://localhost:9001/3.0/lists/bee.example.com/archivers')
        self.assertEqual(cm.exception.code, 404)

    def test_put_bogus_archiver(self):
        # You cannot PUT on an archiver the list doesn't know about.
        with self.assertRaises(HTTPError) as cm:
            call_api(
                'http://localhost:9001/3.0/lists/ant.example.com/archivers', {
                    'bogus-archiver': True,
                    },
                method='PUT')
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason,
                         b'Unexpected parameters: bogus-archiver')

    def test_patch_bogus_archiver(self):
        # You cannot PATCH on an archiver the list doesn't know about.
        with self.assertRaises(HTTPError) as cm:
            call_api(
                'http://localhost:9001/3.0/lists/ant.example.com/archivers', {
                    'bogus-archiver': True,
                    },
                method='PATCH')
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason,
                         b'Unexpected parameters: bogus-archiver')

    def test_put_incomplete_statuses(self):
        # PUT requires the full resource representation.  This one forgets to
        # specify the prototype and mhonarc archiver.
        with self.assertRaises(HTTPError) as cm:
            call_api(
                'http://localhost:9001/3.0/lists/ant.example.com/archivers', {
                    'mail-archive': True,
                    },
                method='PUT')
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason,
                         b'Missing parameters: mhonarc, prototype')

    def test_patch_bogus_status(self):
        # Archiver statuses must be interpretable as booleans.
        with self.assertRaises(HTTPError) as cm:
            call_api(
                'http://localhost:9001/3.0/lists/ant.example.com/archivers', {
                    'mail-archive': 'sure',
                    'mhonarc': False,
                    'prototype': 'no'
                    },
                method='PATCH')
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason, b'Invalid boolean value: sure')


class TestListPagination(unittest.TestCase):
    """Test mailing list pagination functionality.

    We create a bunch of mailing lists within a domain.  When we want to
    get all the lists in that domain via the REST API, we need to
    paginate over them, otherwise there could be too many for display.
    """

    layer = RESTLayer

    def setUp(self):
        with transaction():
            # Create a bunch of mailing lists in the example.com domain.
            create_list('ant@example.com')
            create_list('bee@example.com')
            create_list('cat@example.com')
            create_list('dog@example.com')
            create_list('emu@example.com')
            create_list('fly@example.com')

    def test_first_page(self):
        resource, response = call_api(
            'http://localhost:9001/3.0/domains/example.com/lists'
            '?count=1&page=1')
        # There are 6 total lists, but only the first one in the page.
        self.assertEqual(resource['total_size'], 6)
        self.assertEqual(resource['start'], 0)
        self.assertEqual(len(resource['entries']), 1)
        entry = resource['entries'][0]
        self.assertEqual(entry['fqdn_listname'], 'ant@example.com')

    def test_second_page(self):
        resource, response = call_api(
            'http://localhost:9001/3.0/domains/example.com/lists'
            '?count=1&page=2')
        # There are 6 total lists, but only the first one in the page.
        self.assertEqual(resource['total_size'], 6)
        self.assertEqual(resource['start'], 1)
        self.assertEqual(len(resource['entries']), 1)
        entry = resource['entries'][0]
        self.assertEqual(entry['fqdn_listname'], 'bee@example.com')

    def test_last_page(self):
        resource, response = call_api(
            'http://localhost:9001/3.0/domains/example.com/lists'
            '?count=1&page=6')
        # There are 6 total lists, but only the first one in the page.
        self.assertEqual(resource['total_size'], 6)
        self.assertEqual(resource['start'], 5)
        self.assertEqual(len(resource['entries']), 1)
        entry = resource['entries'][0]
        self.assertEqual(entry['fqdn_listname'], 'fly@example.com')

    def test_zeroth_page(self):
        # Page numbers start at one.
        with self.assertRaises(HTTPError) as cm:
            call_api(
                'http://localhost:9001/3.0/domains/example.com/lists'
                '?count=1&page=0')
        self.assertEqual(cm.exception.code, 400)

    def test_negative_page(self):
        # Negative pages are not allowed.
        with self.assertRaises(HTTPError) as cm:
            call_api(
                'http://localhost:9001/3.0/domains/example.com/lists'
                '?count=1&page=-1')
        self.assertEqual(cm.exception.code, 400)

    def test_past_last_page(self):
        # The 7th page doesn't exist so the collection is empty.
        resource, response = call_api(
            'http://localhost:9001/3.0/domains/example.com/lists'
            '?count=1&page=7')
        # There are 6 total lists, but only the first one in the page.
        self.assertEqual(resource['total_size'], 6)
        self.assertEqual(resource['start'], 6)
        self.assertNotIn('entries', resource)


class TestListDigests(unittest.TestCase):
    """Test /lists/<list-id>/digest"""

    layer = RESTLayer

    def setUp(self):
        with transaction():
            self._mlist = create_list('ant@example.com')
            self._mlist.send_welcome_message = False
            anne = getUtility(IUserManager).create_address('anne@example.com')
            self._mlist.subscribe(anne)
            anne.preferences.delivery_mode = DeliveryMode.plaintext_digests

    def test_bad_digest_url(self):
        with self.assertRaises(HTTPError) as cm:
            call_api(
                'http://localhost:9001/3.0/lists/bogus.example.com/digest')
        self.assertEqual(cm.exception.code, 404)

    def test_post_nothing_to_do(self):
        resource, response = call_api(
            'http://localhost:9001/3.0/lists/ant.example.com/digest', {})
        self.assertEqual(response.status, 200)

    def test_post_something_to_do(self):
        resource, response = call_api(
            'http://localhost:9001/3.0/lists/ant.example.com/digest', dict(
                bump=True))
        self.assertEqual(response.status, 202)

    def test_post_bad_request(self):
        with self.assertRaises(HTTPError) as cm:
            call_api(
                'http://localhost:9001/3.0/lists/ant.example.com/digest', dict(
                    bogus=True))
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason, b'Unexpected parameters: bogus')

    def test_bump_before_send(self):
        with transaction():
            self._mlist.digest_volume_frequency = DigestFrequency.monthly
            self._mlist.volume = 7
            self._mlist.next_digest_number = 4
            self._mlist.digest_last_sent_at = right_now() + timedelta(
                days=-32)
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: message 1

""")
        config.handlers['to-digest'].process(self._mlist, msg, {})
        resource, response = call_api(
            'http://localhost:9001/3.0/lists/ant.example.com/digest', dict(
                send=True,
                bump=True))
        self.assertEqual(response.status, 202)
        make_testable_runner(DigestRunner, 'digest').run()
        # The volume is 8 and the digest number is 2 because a digest was sent
        # after the volume/number was bumped.
        self.assertEqual(self._mlist.volume, 8)
        self.assertEqual(self._mlist.next_digest_number, 2)
        self.assertEqual(self._mlist.digest_last_sent_at, right_now())
        items = get_queue_messages('virgin')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].msg['subject'], 'Ant Digest, Vol 8, Issue 1')
