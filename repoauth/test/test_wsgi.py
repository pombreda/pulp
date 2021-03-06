import unittest
import mock

from pulp.repoauth.wsgi import allow_access


class TestWsgi(unittest.TestCase):

    def setUp(self):
        """
        set up authenticators
        """
        # build authenticators w/ entry points
        self.auth_one = mock.Mock()
        entrypoint_one = mock.Mock()
        entrypoint_one.name = 'auth_one'
        entrypoint_one.load.return_value = self.auth_one

        self.auth_two = mock.Mock()
        entrypoint_two = mock.Mock()
        entrypoint_two.name = 'auth_two'
        entrypoint_two.load.return_value = self.auth_two

        self.entrypoint_list = [entrypoint_one, entrypoint_two]

    @mock.patch('pulp.repoauth.auth_enabled_validation.authenticate')
    def test_auth_disabled(self, auth_enabled):
        """
        Test that we can disable auth via config flag
        """
        # NB: 'True' means that auth is disabled
        auth_enabled.return_value = True
        environ = mock.Mock()

        self.assertTrue(allow_access(environ, 'fake.host.name'))

    @mock.patch('pulp.repoauth.auth_enabled_validation.authenticate')
    @mock.patch('pulp.repoauth.wsgi.iter_entry_points')
    def test_check_entry_points(self, iter_ep, auth_enabled):
        """
        Test that entry points are loaded
        """
        # NB: 'False' means that auth is enabled
        auth_enabled.return_value = False

        environ = mock.Mock()
        iter_ep.return_value = self.entrypoint_list

        self.assertTrue(allow_access(environ, 'fake.host.name'))
        self.auth_one.assert_called_once_with(environ)
        self.auth_two.assert_called_once_with(environ)

    @mock.patch('pulp.repoauth.auth_enabled_validation.authenticate')
    @mock.patch('pulp.repoauth.wsgi.iter_entry_points')
    def test_deny_one(self, iter_ep, auth_enabled):
        """
        Test for when one auth fails but not the other
        """
        # NB: 'False' means that auth is enabled
        auth_enabled.return_value = False

        environ = mock.Mock()
        self.auth_one.return_value = True
        self.auth_two.return_value = False
        iter_ep.return_value = self.entrypoint_list

        self.assertFalse(allow_access(environ, 'fake.host.name'))

    @mock.patch('pulp.repoauth.auth_enabled_validation.authenticate')
    @mock.patch('pulp.repoauth.wsgi.iter_entry_points')
    def test_deny_one_stops_loop(self, iter_ep, auth_enabled):
        """
        Test that we bail out if either authenticator is False
        """
        # NB: 'False' means that auth is enabled
        auth_enabled.return_value = False
        environ = mock.Mock()

        self.auth_one.return_value = False
        self.auth_two.return_value = False
        iter_ep.return_value = self.entrypoint_list

        self.assertFalse(allow_access(environ, 'fake.host.name'))
        # we don't know if auth_one or auth_two will occur first
        total_calls = self.auth_one.call_count + self.auth_two.call_count
        self.assertEquals(total_calls, 1)

    @mock.patch('pulp.repoauth.wsgi.iter_entry_points')
    def test_successful_auth(self, iter_ep):
        """
        Test for when all auth methods succeed
        """
        environ = mock.Mock()

        self.auth_one.return_value = True
        self.auth_two.return_value = True
        iter_ep.return_value = self.entrypoint_list

        self.assertTrue(allow_access(environ, 'fake.host.name'))
