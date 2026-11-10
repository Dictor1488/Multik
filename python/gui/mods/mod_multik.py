# -*- coding: utf-8 -*-
"""INQ Multik: EU/NA/ASIA servers in the stock World of Tanks login list.

All runtime logic and server data live in this file. No replacement XML, extra
pinger, global realm mutation, or disabled client/server version checks.
"""

from __future__ import absolute_import

import functools
import logging
import re

MOD_ID = 'me.inq.multik'
MOD_VERSION = '0.1.0-test1'
_logger = logging.getLogger(MOD_ID)

# Verified against IzeBerg/wot-src EU/NA/ASIA 2.4.1.1 scripts_config.xml.
# realm, name, short name, normal endpoint, token endpoint, periphery ID.
_SERVERS = tuple(
    ('EU', 'WOT EU%d' % n, 'EU%d' % n,
     'login.p%d.worldoftanks.eu:20016' % n,
     'login2.p%d.worldoftanks.eu:20016' % n, 200 + n)
    for n in range(1, 6)
) + (
    ('NA', 'US Central', 'USC', 'wotna3.login.wargaming.net:20016',
     'wotna3-2.login.wargaming.net:20016', 303),
    ('NA', 'South America', 'SA', 'wotna4.login.wargaming.net:20016',
     'wotna4-2.login.wargaming.net:20016', 304),
    ('ASIA', 'WOT ASIA1', 'ASIA1', 'wotasia1.login.wargaming.net:20016',
     'wotasia1-2.login.wargaming.net:20016', 501),
)
_REALMS = ('EU', 'NA', 'ASIA')
_VERSION = re.compile(r'^(eu|na|asia)_([0-9]+(?:\.[0-9]+){2,4}(?:_[0-9]+)?)$')
_DOMAINS = (
    ('EU', re.compile(r'^login2?\.p[0-9]+\.worldoftanks\.eu$')),
    ('NA', re.compile(r'^wotna[0-9]+(?:-2)?\.login\.wargaming\.net$')),
    ('ASIA', re.compile(r'^wotasia[0-9]+(?:-2)?\.login\.wargaming\.net$')),
)
try:
    _TEXT = (basestring,)
except NameError:
    _TEXT = (str,)

_enabled = False
_hooks = []
_added_hosts = []
_hosts = None
_connection = None
_login_manager = None
_predefined = None
_base_realm = None
_last_realm = None
_ping_subscription = False
_disconnect_subscription = False
_logged_on_subscription = False


def _region(endpoint):
    if not isinstance(endpoint, _TEXT):
        return None
    host = endpoint.lower().rsplit(':', 1)[0]
    for realm, pattern in _DOMAINS:
        if pattern.match(host):
            return realm
    return None


def _client_version(version, realm):
    if realm not in _REALMS or not isinstance(version, _TEXT):
        return version
    match = _VERSION.match(version)
    if match is None:
        return version
    return realm.lower() + '_' + match.group(2)


def _hook(cls, name, handler):
    original = getattr(cls, name)
    active = [True]

    @functools.wraps(original)
    def wrapped(*args, **kwargs):
        if not _enabled or not active[0]:
            return original(*args, **kwargs)
        return handler(original, *args, **kwargs)

    setattr(cls, name, wrapped)
    _hooks.append((cls, name, original, wrapped, active))


def _refresh():
    if _login_manager is not None:
        servers = _login_manager.servers
        servers.updateServerList()
        servers.onServersStatusChanged(servers.serverList)


def _add_servers():
    # Plan the complete update before changing any live lookup table.
    items = list(_hosts._hosts)
    urls = dict(_hosts._urlMap)
    names = dict(_hosts._nameMap)
    ids = dict(_hosts._peripheryMap)
    added = []
    for realm, name, short, url, token, periphery in _SERVERS:
        if url in urls:
            # Keep authoritative native/other-mod definitions unchanged.
            continue
        if token in urls or name in names or periphery in ids:
            _logger.warning('[INQ Multik] Conflicting server skipped: %s', name)
            continue
        item = _hosts._makeHostItem(
            name, short, url, urlToken=token,
            keyPath='loginapp_wot.pubkey', peripheryID=periphery
        )
        index = len(items)
        items.append(item)
        urls[url] = index
        urls[token] = index
        names[name] = index
        ids[periphery] = index
        added.append(item)
    _hosts._hosts = items
    _hosts._urlMap = urls
    _hosts._nameMap = names
    _hosts._peripheryMap = ids
    _added_hosts.extend(added)


def _remove_servers():
    # Preserve aliases and any servers registered by other mods after our init.
    removed = set(id(item) for item in _added_hosts)
    current = _hosts._hosts
    positions = {}
    kept = []
    for old_index, item in enumerate(current):
        if id(item) not in removed:
            positions[old_index] = len(kept)
            kept.append(item)
    for field in ('_urlMap', '_nameMap', '_peripheryMap'):
        old_map = getattr(_hosts, field)
        new_map = dict((key, positions[index]) for key, index in old_map.items()
                       if index in positions)
        setattr(_hosts, field, new_map)
    _hosts._hosts = kept
    del _added_hosts[:]


def _host_list(base, self, hosts, *args, **kwargs):
    result = base(self, hosts, *args, **kwargs)
    if self is not _hosts:
        return result
    corrected = []
    for row in result:
        realm = _region(row[0])
        if realm is not None and realm != _base_realm:
            # The native CSIS URL describes the base region only.
            row = list(row)
            row[-2] = _predefined.HOST_AVAILABILITY.UNKNOWN
            row = tuple(row)
        corrected.append(row)
    return corrected


def _choose_auto():
    realm = _last_realm or _base_realm
    candidates = [host for host in _hosts.hosts() if _region(host.url) == realm]
    if not candidates:
        return None
    statuses = dict((row[0], row[-2])
                    for row in _hosts.getSimpleHostsList(candidates))
    available = [host for host in candidates
                 if statuses.get(host.url) != _predefined.HOST_AVAILABILITY.NOT_AVAILABLE]
    if not available:
        return None

    def score(host):
        value, state = _hosts.getHostPingData(host.url)
        # Native ping cache can retain an old value with UNDEFINED status.
        valid = (state in (_predefined.PING_STATUSES.LOW,
                           _predefined.PING_STATUSES.NORM,
                           _predefined.PING_STATUSES.HIGH) and value >= 0)
        recommended = statuses.get(host.url) == _predefined.HOST_AVAILABILITY.RECOMMENDED
        return (not recommended, not valid, value if valid else 0)

    return min(available, key=score)


def _resolve(endpoint):
    global _last_realm
    if endpoint == _predefined.AUTO_LOGIN_QUERY_URL:
        selected = _choose_auto()
        if selected is not None:
            endpoint = selected.url
    realm = _region(endpoint)
    if realm is not None:
        _last_realm = realm
    return endpoint


def _auto_query(base, self, callback):
    if self is not _hosts:
        return base(self, callback)
    if callback is None:
        return None
    selected = _choose_auto()
    if selected is None:
        # Do not fall back to an Auto search across unrelated regions.
        _reject(_connection, 'LOGIN_REJECTED_SERVER_NOT_READY')
        return None
    return callback(selected)


def _get_host(base, self, authMethod, hostName):
    if hostName == _predefined.AUTO_LOGIN_QUERY_URL:
        # Resolve before credentials/token data are prepared, not afterwards.
        return _resolve(hostName)
    _resolve(hostName)
    return base(self, authMethod, hostName)


def _connect(base, self, params, password, serverName):
    serverName = _resolve(serverName)
    if serverName == _predefined.AUTO_LOGIN_QUERY_URL:
        _reject(self, 'LOGIN_REJECTED_SERVER_NOT_READY')
        return None
    realm = _region(serverName)
    previous = getattr(self, '_multik_realm', None)
    if realm is not None and previous != realm:
        self.setPeripheryRoutingGroup(None, None)
    self._multik_realm = realm
    # Preserve native auth_realm and WGC data, following RaJCeL's narrow model.
    return base(self, params, password, serverName)


def _check_versions(base, self, clientVersion, serverVersion):
    corrected = _client_version(clientVersion, getattr(self, '_multik_realm', None))
    _logger.debug('[INQ Multik] Version check: %s -> %s; server %s',
                  clientVersion, corrected, serverVersion)
    return base(self, corrected, serverVersion)


def _reject(connection, status):
    from connection_mgr import LOGIN_STATUS
    from gui.Scaleform.Waiting import Waiting
    Waiting.hide('login')
    connection.onRejected(getattr(LOGIN_STATUS, status), {})


def _login(base, self, email, password, serverName,
           isSocialToken2Login, rememberUser):
    preferences = self._preferences
    previous = (preferences.get('multik_token_realm') or
                _region(preferences.get('server_name')) or _base_realm)
    serverName = _resolve(serverName)
    target = _region(serverName)
    if target is not None and previous != target:
        self.connectionMgr.setPeripheryRoutingGroup(None, None)
        # Old periphery pinning and token2 belong to the previous region.
        preferences['peripheryLifetime'] = ''
        if preferences.get('token2'):
            self.clearToken2Preference()
            preferences['password_length'] = 0
            self.writePreferences()
            # A masked saved password is not a usable plaintext credential.
            if isSocialToken2Login or not password or not password.strip('*'):
                _reject(self.connectionMgr, 'SESSION_END')
                return None
            isSocialToken2Login = False
    return base(self, email, password, serverName,
                isSocialToken2Login, rememberUser)


def _remember_region(responseData):
    # Manager's original bound handler was subscribed before GUI mods loaded.
    # Subscribe after it instead of patching a method the event already captured.
    realm = getattr(_connection, '_multik_realm', None)
    if realm in _REALMS:
        _login_manager._preferences['multik_token_realm'] = realm
        _login_manager.writePreferences()


def _disconnected(*args):
    if not _enabled:
        return
    _connection._multik_realm = None
    _connection.setPeripheryRoutingGroup(None, None)
    _hosts.resetQueryResult()


def _ping_ready(*args):
    global _ping_subscription
    if _ping_subscription:
        _hosts.onPingPerformed -= _ping_ready
        _ping_subscription = False
    if not _enabled:
        return
    # Existing native request may have started before foreign hosts were added.
    requester = _hosts._PreDefinedHostList__pingRequester
    requester._PingRequester__lastUpdateTime = 0
    _hosts.requestPing(True)


def _refresh_ping():
    global _ping_subscription
    requester = _hosts._PreDefinedHostList__pingRequester
    if requester._PingRequester__isRequestPingInProgress:
        _hosts.onPingPerformed += _ping_ready
        _ping_subscription = True
    else:
        _ping_ready()


def init():
    global _enabled, _hosts, _connection, _login_manager, _predefined
    global _base_realm, _last_realm
    global _disconnect_subscription, _logged_on_subscription
    if _enabled:
        return
    try:
        import constants
        import predefined_hosts
        from connection_mgr import ConnectionManager
        from gui.login.Manager import Manager
        from helpers import dependency
        from skeletons.connection_mgr import IConnectionManager
        from skeletons.gui.login_manager import ILoginManager

        _base_realm = constants.CURRENT_REALM
        if _base_realm not in _REALMS:
            _logger.warning('[INQ Multik] Unsupported base realm: %s', _base_realm)
            return
        _predefined = predefined_hosts
        _hosts = predefined_hosts.g_preDefinedHosts
        _connection = dependency.instance(IConnectionManager)
        _login_manager = dependency.instance(ILoginManager)
        if not _hosts._isDataLoaded:
            raise RuntimeError('Native server registry is not initialized')
        for field in ('_hosts', '_urlMap', '_nameMap', '_peripheryMap'):
            if not hasattr(_hosts, field):
                raise RuntimeError('Unsupported native server registry: ' + field)
        requester = _hosts._PreDefinedHostList__pingRequester
        for field in ('_PingRequester__lastUpdateTime',
                      '_PingRequester__isRequestPingInProgress'):
            if not hasattr(requester, field):
                raise RuntimeError('Unsupported native ping interface: ' + field)

        preferences = _login_manager._preferences
        remembered = preferences.get('multik_token_realm')
        _last_realm = (_region(preferences.get('server_name')) or
                       (remembered if remembered in _REALMS else _base_realm))
        hooks = (
            (type(_hosts), 'getSimpleHostsList', _host_list),
            (type(_hosts), 'autoLoginQuery', _auto_query),
            (ConnectionManager, 'initiateConnection', _connect),
            (ConnectionManager, 'checkClientServerVersions', _check_versions),
            (Manager, '_getHost', _get_host),
            (Manager, 'initiateLogin', _login),
        )
        for cls, name, handler in hooks:
            if not callable(getattr(cls, name, None)):
                raise RuntimeError('Unsupported native method: ' + name)
        _enabled = True
        for cls, name, handler in hooks:
            _hook(cls, name, handler)
        _add_servers()
        _connection.onDisconnected += _disconnected
        _disconnect_subscription = True
        _connection.onLoggedOn += _remember_region
        _logged_on_subscription = True
        _hosts.resetQueryResult()
        _refresh()
        _refresh_ping()
        _logger.info('[INQ Multik] %s loaded: %d servers', MOD_VERSION, len(_hosts.hosts()))
    except Exception:
        _logger.exception('[INQ Multik] Initialization failed; reverting our changes')
        fini()


def fini():
    global _enabled, _ping_subscription, _disconnect_subscription, _logged_on_subscription
    was_enabled = _enabled
    _enabled = False
    if _hosts is not None and _ping_subscription:
        _hosts.onPingPerformed -= _ping_ready
        _ping_subscription = False
    if _connection is not None and _disconnect_subscription:
        _connection.onDisconnected -= _disconnected
        _disconnect_subscription = False
        _connection._multik_realm = None
    if _connection is not None and _logged_on_subscription:
        _connection.onLoggedOn -= _remember_region
        _logged_on_subscription = False
    for cls, name, original, wrapped, active in reversed(_hooks):
        active[0] = False
        # A later mod may have wrapped our wrapper; leave its chain intact.
        current = getattr(cls, name, None)
        # Python 2 returns an unbound method here; Python 3 returns the function.
        if getattr(current, 'im_func', current) is wrapped:
            setattr(cls, name, original)
    del _hooks[:]
    if _hosts is not None and _added_hosts:
        _remove_servers()
    if was_enabled and _login_manager is not None:
        try:
            _refresh()
        except Exception:
            _logger.debug('[INQ Multik] Login services already stopped', exc_info=True)

