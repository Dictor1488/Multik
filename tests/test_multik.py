"""Contract tests for the single-file mod, using WoT-shaped API doubles.

These verify integration behavior, not actual authorization or engine resources.
"""
import importlib.util
from collections import namedtuple
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'python/gui/mods/mod_multik.py'
Host = namedtuple('Host', 'name shortName url urlToken urlIterator keyPath areaID peripheryID')


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        if handler not in self.handlers:
            self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)
        return self

    def __call__(self, *args):
        for handler in list(self.handlers):
            handler(*args)


def load_mod():
    spec = importlib.util.spec_from_file_location('multik_under_test', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def world(mod, realm='EU', pending_ping=False):
    class Availability:
        NOT_AVAILABLE = -1
        NOT_RECOMMENDED = 0
        RECOMMENDED = 1
        REQUESTED = 2
        IGNORED = 3
        UNKNOWN = 4

    class PingStatus:
        UNDEFINED = 0
        HIGH = 1
        NORM = 2
        LOW = 3
        REQUESTED = 4

    class Registry:
        constructions = 0

        def __init__(self):
            Registry.constructions += 1
            self._hosts = []
            self._urlMap = {}
            self._nameMap = {}
            self._peripheryMap = {}
            self._isDataLoaded = True
            self.onPingPerformed = Event()
            self.pings = {}
            self.statuses = {}
            self.requests = []
            self._PreDefinedHostList__pingRequester = types.SimpleNamespace(**{
                '_PingRequester__lastUpdateTime': 100,
                '_PingRequester__isRequestPingInProgress': pending_ping,
            })
            for r, name, short, url, token, pid in mod._SERVERS:
                if r == realm:
                    self.append(self._makeHostItem(
                        name, short, url, urlToken=token,
                        keyPath='native-key', peripheryID=pid))
                    self.statuses[url] = Availability.RECOMMENDED

        def append(self, host):
            idx = len(self._hosts)
            self._hosts.append(host)
            self._urlMap[host.url] = idx
            self._urlMap[host.urlToken] = idx
            self._nameMap[host.name] = idx
            self._peripheryMap[host.peripheryID] = idx

        def _makeHostItem(self, name, shortName, url, urlToken='',
                          urlIterator=None, keyPath=None, areaID=None, peripheryID=0):
            return Host(name, shortName, url, urlToken, urlIterator,
                        keyPath, areaID, peripheryID)

        def hosts(self):
            return list(self._hosts)

        def getSimpleHostsList(self, hosts, withShortName=False):
            result = []
            for host in hosts:
                # Simulate base CSIS not knowing a foreign periphery.
                status = self.statuses.get(host.url, Availability.NOT_AVAILABLE)
                if withShortName:
                    result.append((host.url, host.name, host.shortName, status, host.peripheryID))
                else:
                    result.append((host.url, host.name, status, host.peripheryID))
            return result

        def autoLoginQuery(self, callback):
            return callback(self._hosts[0])

        def getHostPingData(self, url):
            return self.pings.get(url, (-1, PingStatus.UNDEFINED))

        def resetQueryResult(self):
            self.pings.clear()

        def requestPing(self, forced=False):
            self.requests.append((forced, [h.url for h in self._hosts]))

    class Connection:
        def __init__(self):
            self.onDisconnected = Event()
            self.onLoggedOn = Event()
            self.onRejected = Event()
            self.calls = []
            self.checked = []
            self.routing = []
            self.content_complete = True

        def setPeripheryRoutingGroup(self, group, available):
            self.routing.append((group, available))

        def initiateConnection(self, params, password, serverName):
            if 'auth_realm' not in params:
                params['auth_realm'] = realm
            self.calls.append((dict(params), password, serverName))
            return 'native-connect-result'

        def checkClientServerVersions(self, clientVersion, serverVersion):
            self.checked.append((clientVersion, serverVersion))
            # A strict stock stand-in makes accidental version bypass observable.
            return clientVersion == serverVersion and self.content_complete

    registry = Registry()
    connection = Connection()

    class Servers:
        def __init__(self):
            self.onServersStatusChanged = Event()
            self.serverList = []

        def updateServerList(self):
            self.serverList = registry.getSimpleHostsList(registry.hosts())

    class Manager:
        def __init__(self):
            self._preferences = {'server_name': registry.hosts()[0].url,
                                 'token2': '', 'multik_token_realm': realm}
            self.connectionMgr = connection
            self.servers = Servers()
            self.writes = 0

        def _getHost(self, authMethod, hostName):
            # Models native peripheryLifetime pinning for Auto.
            return self._preferences.get('pinned', hostName) if hostName == 'auto.login.app:0000' else hostName

        def initiateLogin(self, email, password, serverName,
                          isSocialToken2Login, rememberUser):
            token = self._preferences.get('token2')
            params = {'login': email, 'auth_method': 'token2' if token else 'basic'}
            if token:
                params['token2'] = token
            resolved = self._getHost(params['auth_method'], serverName)
            self._preferences['server_name'] = resolved
            return self.connectionMgr.initiateConnection(params, password, resolved)

        def clearToken2Preference(self):
            self._preferences['token2'] = ''

        def writePreferences(self):
            self.writes += 1

        def _onLoggedOn(self, responseData):
            self._preferences['token2'] = responseData.get('token2', '')
            return 'native-logged-on-result'

    manager = Manager()
    # The real Manager subscribes its bound callback before gui.mods.init.
    connection.onLoggedOn += manager._onLoggedOn
    hidden = []
    predef = types.ModuleType('predefined_hosts')
    predef.g_preDefinedHosts = registry
    predef.AUTO_LOGIN_QUERY_URL = 'auto.login.app:0000'
    predef.HOST_AVAILABILITY = Availability
    predef.PING_STATUSES = PingStatus
    conn_module = types.ModuleType('connection_mgr')
    conn_module.ConnectionManager = Connection
    conn_module.LOGIN_STATUS = types.SimpleNamespace(
        SESSION_END='SESSION_END',
        LOGIN_REJECTED_SERVER_NOT_READY='LOGIN_REJECTED_SERVER_NOT_READY')
    i_connection, i_login = object(), object()
    objects = {i_connection: connection, i_login: manager}

    def module(name, **attributes):
        result = types.ModuleType(name)
        result.__path__ = []
        result.__dict__.update(attributes)
        return result

    modules = {
        'constants': module('constants', CURRENT_REALM=realm),
        'predefined_hosts': predef,
        'connection_mgr': conn_module,
        'gui': module('gui'), 'gui.login': module('gui.login'),
        'gui.login.Manager': module('gui.login.Manager', Manager=Manager),
        'gui.Scaleform': module('gui.Scaleform'),
        'gui.Scaleform.Waiting': module('gui.Scaleform.Waiting',
                                      Waiting=types.SimpleNamespace(hide=hidden.append)),
        'helpers': module('helpers',
                          dependency=types.SimpleNamespace(instance=objects.__getitem__)),
        'skeletons': module('skeletons'),
        'skeletons.gui': module('skeletons.gui'),
        'skeletons.connection_mgr': module('skeletons.connection_mgr',
                                         IConnectionManager=i_connection),
        'skeletons.gui.login_manager': module('skeletons.gui.login_manager',
                                            ILoginManager=i_login),
    }
    return types.SimpleNamespace(registry=registry, connection=connection, manager=manager,
                                 modules=modules, Registry=Registry, Connection=Connection,
                                 Manager=Manager, hidden=hidden, availability=Availability)


class MultikTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_mod()
        self.w = world(self.mod)
        self.modules = patch.dict(sys.modules, self.w.modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.addCleanup(self.mod.fini)

    def start(self):
        self.mod.init()
        self.assertTrue(self.mod._enabled)

    def endpoint(self, realm, token=False):
        return next(row[4 if token else 3] for row in self.mod._SERVERS if row[0] == realm)

    def test_registers_eight_servers_once_preserving_native_identity_and_keys(self):
        registry = self.w.registry
        native = registry.hosts()[0]
        self.start()
        self.mod.init()
        self.assertIs(self.mod._hosts, registry)
        self.assertEqual(len(registry.hosts()), 8)
        self.assertIs(registry.hosts()[0], native)
        self.assertEqual(native.keyPath, 'native-key')
        self.assertEqual(self.w.Registry.constructions, 1)
        self.assertEqual(len(self.w.manager.servers.serverList), 8)

    def test_aliases_and_periphery_lookup_agree(self):
        self.start()
        r = self.w.registry
        for h in r.hosts():
            idx = r._urlMap[h.url]
            self.assertIs(r._hosts[idx], h)
            self.assertEqual(idx, r._urlMap[h.urlToken])
            self.assertEqual(idx, r._nameMap[h.name])
            self.assertEqual(idx, r._peripheryMap[h.peripheryID])

    def test_conflicting_periphery_is_preserved_not_overwritten(self):
        self.w.registry._peripheryMap[303] = 0
        with self.assertLogs('me.inq.multik', level='WARNING'):
            self.start()
        self.assertEqual(len(self.w.registry.hosts()), 7)
        self.assertEqual(self.w.registry._peripheryMap[303], 0)

    def test_stock_calls_receive_region_adjustment_and_return_values(self):
        self.start()
        c = self.w.connection
        for region in ('EU', 'NA', 'ASIA', 'EU'):
            self.assertEqual(c.initiateConnection({}, 'password', self.endpoint(region)),
                             'native-connect-result')
            self.assertTrue(c.checkClientServerVersions('eu_2.4.1_3',
                                                       region.lower() + '_2.4.1_3'))
        self.assertEqual(len(c.calls), 4)
        self.assertEqual(c.checked[-1], ('eu_2.4.1_3', 'eu_2.4.1_3'))

    def test_does_not_bypass_numeric_version_or_content_checks(self):
        self.start()
        c = self.w.connection
        c.initiateConnection({}, '', self.endpoint('NA'))
        self.assertFalse(c.checkClientServerVersions('eu_2.4.1_3', 'na_2.4.2_3'))
        self.assertFalse(c.checkClientServerVersions('eu_2.4.1_3', 'na_2.4.1_4'))
        c.content_complete = False
        self.assertFalse(c.checkClientServerVersions('eu_2.4.1_3', 'na_2.4.1_3'))

    def test_unknown_endpoints_and_version_formats_are_untouched(self):
        self.start()
        c = self.w.connection
        c.initiateConnection({}, '', 'login.p1.worldoftanks.eu.evil.test:20016')
        self.assertFalse(c.checkClientServerVersions('eu_2.4.1_3', 'na_2.4.1_3'))
        for value in (None, 'future-format', 'ru_2.4.1_3'):
            self.assertEqual(self.mod._client_version(value, 'NA'), value)
        self.assertIsNone(self.mod._region('auto.login.app:0000'))

    def test_token_endpoint_identifies_correct_region_without_changing_auth_data(self):
        self.start()
        c = self.w.connection
        params = {'auth_realm': 'native-auth-realm', 'token2': 'secret'}
        c.initiateConnection(params, '', self.endpoint('ASIA', token=True))
        self.assertEqual(c._multik_realm, 'ASIA')
        self.assertEqual(c.calls[0][0], params)
        self.assertEqual(self.w.modules['constants'].CURRENT_REALM, 'EU')

    def test_foreign_csis_is_unknown_in_both_stock_row_formats(self):
        self.start()
        r = self.w.registry
        for with_short in (False, True):
            rows = r.getSimpleHostsList(r.hosts(), with_short)
            statuses = {self.mod._region(row[0]): row[-2] for row in rows}
            self.assertEqual(statuses['EU'], self.w.availability.RECOMMENDED)
            self.assertEqual(statuses['NA'], self.w.availability.UNKNOWN)
            self.assertEqual(statuses['ASIA'], self.w.availability.UNKNOWN)

    def test_auto_stays_in_last_region_and_uses_valid_cached_ping(self):
        self.start()
        self.mod._resolve(self.endpoint('NA'))
        na = [h for h in self.w.registry.hosts() if self.mod._region(h.url) == 'NA']
        self.w.registry.pings = {na[0].url: (150, 1), na[1].url: (50, 3)}
        selected = []
        self.w.registry.autoLoginQuery(selected.append)
        self.assertEqual(selected, [na[1]])

    def test_auto_ignores_stale_ping_values_and_native_old_region_pinning(self):
        self.start()
        self.mod._resolve(self.endpoint('NA'))
        na = [h for h in self.w.registry.hosts() if self.mod._region(h.url) == 'NA']
        self.w.registry.pings = {na[0].url: (1, 0), na[1].url: (100, 2)}
        self.w.manager._preferences['pinned'] = self.endpoint('EU')
        selected = self.w.manager._getHost('basic', 'auto.login.app:0000')
        self.assertEqual(selected, na[1].url)

    def test_all_unavailable_auto_does_not_choose_another_region(self):
        self.start()
        for h in self.w.registry.hosts():
            if self.mod._region(h.url) == 'EU':
                self.w.registry.statuses[h.url] = -1
        rejected, selected = [], []
        self.w.connection.onRejected += lambda *args: rejected.append(args)
        self.w.registry.autoLoginQuery(selected.append)
        self.assertFalse(selected)
        self.assertEqual(rejected[0][0], 'LOGIN_REJECTED_SERVER_NOT_READY')

    def test_foreign_saved_token_and_masked_password_require_new_login(self):
        self.start()
        manager = self.w.manager
        manager._preferences.update(token2='eu-token', password_length=8)
        rejected = []
        self.w.connection.onRejected += lambda *a: rejected.append(a)
        manager.initiateLogin('user', '********', self.endpoint('NA'), False, True)
        self.assertFalse(self.w.connection.calls)
        self.assertEqual(manager._preferences['token2'], '')
        self.assertEqual(rejected[0][0], 'SESSION_END')
        self.assertIn('login', self.w.hidden)

    def test_foreign_saved_token_with_real_password_uses_basic_login(self):
        self.start()
        manager = self.w.manager
        manager._preferences.update(token2='eu-token', peripheryLifetime='old-pin')
        manager.initiateLogin('user', 'new-password', self.endpoint('ASIA'), False, True)
        params, password, host = self.w.connection.calls[0]
        self.assertEqual(params['auth_method'], 'basic')
        self.assertNotIn('token2', params)
        self.assertEqual(manager._preferences['peripheryLifetime'], '')

    def test_same_region_token_is_preserved(self):
        self.start()
        self.w.manager._preferences['token2'] = 'eu-token'
        self.w.manager.initiateLogin('user', '********', self.endpoint('EU'), False, True)
        self.assertEqual(self.w.connection.calls[0][0]['token2'], 'eu-token')

    def test_success_marks_token_region_after_stock_preferences_update(self):
        self.start()
        self.w.connection.initiateConnection({}, '', self.endpoint('NA'))
        self.w.connection.onLoggedOn({'token2': 'na-token'})
        self.assertEqual(self.w.manager._preferences['multik_token_realm'], 'NA')
        self.assertEqual(self.w.manager._preferences['token2'], 'na-token')

    def test_disconnect_resets_routing_but_retains_expected_auto_region(self):
        self.start()
        c = self.w.connection
        c.initiateConnection({}, '', self.endpoint('ASIA'))
        c.onDisconnected()
        self.assertIsNone(c._multik_realm)
        self.assertEqual(c.routing[-1], (None, None))
        self.assertEqual(self.mod._last_realm, 'ASIA')
        c.initiateConnection({}, '', self.endpoint('EU'))
        self.assertEqual(c._multik_realm, 'EU')

    def test_pending_native_ping_is_refreshed_once_with_all_hosts(self):
        self.w.registry._PreDefinedHostList__pingRequester._PingRequester__isRequestPingInProgress = True
        self.start()
        self.assertEqual(self.w.registry.requests, [])
        requester = self.w.registry._PreDefinedHostList__pingRequester
        requester._PingRequester__isRequestPingInProgress = False
        self.w.registry.onPingPerformed()
        self.assertEqual(len(self.w.registry.requests), 1)
        self.assertEqual(len(self.w.registry.requests[0][1]), 8)
        self.w.registry.onPingPerformed()
        self.assertEqual(len(self.w.registry.requests), 1)

    def test_fini_preserves_other_mod_servers_and_aliases(self):
        original_connect = self.w.Connection.initiateConnection
        self.start()
        r = self.w.registry
        extra = r._makeHostItem('Other', 'Other', 'other.test:20016', peripheryID=999)
        r.append(extra)
        r._urlMap['other-alias:20016'] = len(r._hosts) - 1
        self.mod.fini()
        self.assertEqual(len(r.hosts()), 6)
        self.assertIs(r._hosts[r._urlMap['other-alias:20016']], extra)
        self.assertIs(self.w.Connection.initiateConnection, original_connect)
        self.assertFalse(self.w.connection.onDisconnected.handlers)

    def test_later_wrapper_survives_fini_and_reinitialization(self):
        self.start()
        wrapped = self.w.Connection.checkClientServerVersions
        calls = []
        def other(instance, client, server):
            calls.append(client)
            return wrapped(instance, client, server)
        self.w.Connection.checkClientServerVersions = other
        self.mod.fini()
        self.assertIs(self.w.Connection.checkClientServerVersions, other)
        self.start()
        self.w.connection.initiateConnection({}, '', self.endpoint('NA'))
        self.assertTrue(self.w.connection.checkClientServerVersions('eu_2.4.1_3', 'na_2.4.1_3'))
        self.assertEqual(calls, ['na_2.4.1_3'])

    def test_init_failure_rolls_back_servers_hooks_and_subscriptions(self):
        original = self.w.Connection.initiateConnection
        def fail_ping(*args):
            raise RuntimeError('ping failed')
        self.w.registry.requestPing = fail_ping
        with self.assertLogs('me.inq.multik', level='ERROR'):
            self.mod.init()
        self.assertFalse(self.mod._enabled)
        self.assertEqual(len(self.w.registry.hosts()), 5)
        self.assertIs(self.w.Connection.initiateConnection, original)
        self.assertFalse(self.w.connection.onDisconnected.handlers)

    def test_incompatible_client_does_not_install_partial_hooks(self):
        del self.w.registry._peripheryMap
        with self.assertLogs('me.inq.multik', level='ERROR'):
            self.mod.init()
        self.assertFalse(self.mod._enabled)
        self.assertEqual(self.mod._hooks, [])


class OtherBaseRealms(unittest.TestCase):
    def test_na_and_asia_clients_keep_their_native_hosts(self):
        for realm in ('NA', 'ASIA'):
            mod = load_mod()
            w = world(mod, realm)
            native = w.registry.hosts()[0]
            with patch.dict(sys.modules, w.modules):
                try:
                    mod.init()
                    self.assertTrue(mod._enabled)
                    self.assertEqual(len(w.registry.hosts()), 8)
                    self.assertIs(w.registry.hosts()[0], native)
                    self.assertEqual(mod._region(mod._choose_auto().url), realm)
                finally:
                    mod.fini()

    def test_unsupported_realm_is_not_modified(self):
        mod = load_mod()
        w = world(mod)
        w.modules['constants'].CURRENT_REALM = 'RU'
        with patch.dict(sys.modules, w.modules):
            with self.assertLogs('me.inq.multik', level='WARNING'):
                mod.init()
        self.assertFalse(mod._enabled)
        self.assertEqual(len(w.registry.hosts()), 5)


if __name__ == '__main__':
    unittest.main()

