import collections
import logging
import netsnmp


ResultTuple = collections.namedtuple('ResultTuple', ['value', 'type'])


class Error(Exception):
  """Base error class for this module."""


class TimeoutError(Error):
  """Timeout talking to the device."""


class NoModelOid(Error):
  """Could not locate a model for the switch."""


class SnmpError(Error):
  """A SNMP error occurred."""


class SnmpTarget(object):

  def __init__(self, host, ip, timestamp, layer, version, community=None,
      user=None, auth_proto=None, auth=None, priv_proto=None, priv=None,
      sec_level=None,
      port=161):
    self._full_host = "%s:%s" % (ip, port)
    self._max_size = 256
    self.host=host
    self.ip=ip
    self.timestamp=timestamp
    self.layer=layer
    self.version=version
    self.community=community
    self.user=user
    self.auth_proto=auth_proto
    self.auth=auth
    self.priv_proto=priv_proto
    self.priv=priv
    self.sec_level=sec_level

  def _snmp_session(self, vlan=None, timeout=1000000, retries=3):
    if self.version == 3:
      context = ('vlan-%s' % vlan) if vlan else ''
      return netsnmp.Session(Version=3, DestHost=self._full_host,
        SecName=self.user, SecLevel=self.sec_level, Context=context,
        AuthProto=self.auth_proto, AuthPass=self.auth,
        PrivProto=self.priv_proto, PrivPass=self.priv,
        UseNumeric=1, Timeout=timeout, Retries=retries)
    else:
      community = ('%s@%s' % (self.community, vlan)) if vlan else self.community
      return netsnmp.Session(Version=self.version, DestHost=self._full_host,
          Community=community, UseNumeric=1, Timeout=timeout,
          Retries=retries)

  def walk(self, oid, vlan=None):
    sess = self._snmp_session(vlan)
    ret = {}
    nextoid = oid
    offset = 0

    suffix = ('@%s' % vlan) if vlan else ''

    # Abort the walk when it exits the OID tree we are interested in
    while nextoid.startswith(oid):
      var_list = netsnmp.VarList(netsnmp.Varbind(nextoid, offset))
      sess.getbulk(nonrepeaters=0, maxrepetitions=self._max_size,
                   varlist=var_list)

      # WORKAROUND FOR NEXUS BUG (2014-11-24)
      # Indy told blueCmd that Nexus silently drops the SNMP response
      # if the packet is fragmented. Try with large size first, but drop down
      # to smaller one.
      if sess.ErrorStr == 'Timeout':
        if self._max_size == 1:
          raise TimeoutError(
              'Timeout getting %s from %s' % (nextoid, self.host))
        self._max_size = int(self._max_size / 16)
        continue
      if sess.ErrorStr != '':
        raise SnmpError('SNMP error while walking host %s: %s' % (
          self.host, sess.ErrorStr))

      for result in var_list:
        currentoid = '%s.%s' % (result.tag, int(result.iid))
        # We don't want to save extra oids that the bulk walk might have
        # contained.
        if not currentoid.startswith(oid):
          break
        ret[currentoid + suffix] = ResultTuple(result.val, result.type)
      # Continue bulk walk
      offset = int(var_list[-1].iid)
      nextoid = var_list[-1].tag
    return ret

  def get(self, oid):
    var = netsnmp.Varbind(oid)
    var_list = netsnmp.VarList(var)
    sess = self._snmp_session(timeout=500000, retries=2)
    sess.get(var_list)
    if sess.ErrorStr != '':
      if sess.ErrorStr == 'Timeout':
        raise TimeoutError('Timeout getting %s from %s' % (oid, self.host))
      raise SnmpError('SNMP error while talking to host %s: %s' % (
        self.host, sess.ErrorStr))

    return {var.tag: ResultTuple(var.val, var.type)}

  def model(self):
    model_oids = [
        '.1.3.6.1.2.1.47.1.1.1.1.13.1',     # Normal switches
        '.1.3.6.1.2.1.47.1.1.1.1.13.1001',  # Stacked switches
        '.1.3.6.1.2.1.47.1.1.1.1.13.10',    # Nexus
    ]
    for oid in model_oids:
      model = self.get(oid)
      if not model:
        continue
      value = model.values().pop().value
      if value:
        return value
    raise NoModelOid('No model OID contained a model')

  def vlans(self):
    try:
      oids = self.walk('.1.3.6.1.4.1.9.9.46.1.3.1.1.2').keys()
      vlans = {int(x.split('.')[-1]) for x in oids}
      return vlans
    except ValueError, e:
      logging.info('ValueError while parsing VLAN for %s: %s', self.host, e)
      return []
