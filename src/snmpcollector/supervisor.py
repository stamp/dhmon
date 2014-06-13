import Queue
import logging
import multiprocessing as mp
import sqlite3
import yaml

import snmp_target


class Supervisor(object):

  STOP_TOKEN = None
  TICK_TOKEN = 'TICK'

  def __init__(self, config='/etc/snmpcollector.yaml'):
    logging.info('Starting supervisor')
    self.config = yaml.load(file(config, 'r'))
    self.control_queue = mp.JoinableQueue()
    self.work_queue = mp.JoinableQueue()
    p = mp.Process(target=self.worker, args=())
    p.start()

  def stop(self):
    self.control_queue.put(self.STOP_TOKEN)
    self.control_queue.join()

  def tick(self, signum=None, frame=None):
    logging.debug('Received tick, starting new poll cycle')
    self.control_queue.put(self.TICK_TOKEN)

  def _construct_targets(self):
    db = sqlite3.connect('/etc/ipplan.db')
    cursor = db.cursor()
    sql = ("SELECT h.name, o.value FROM host h, option o WHERE o.name = 'layer'"
        "AND h.node_id = o.node_id")
    nodes = {}
    for host, layer in cursor.execute( sql ).fetchall():
      layer_config = self.config['snmp'].get(layer, None)
      if layer_config is None:
        logging.error('Unable to target "%s" since layer "%s" is unknown',
            host, layer)
        continue
      nodes[host] = snmp_target.SnmpTarget(host, **layer_config)
    return nodes

  def _new_cycle(self):
    for target in self._construct_targets().values():
      self.work_queue.put(target)
    logging.debug('New work pushed, length %d', self.work_queue.qsize())

  def worker(self):
    logging.info('Started supervisor')
    running = True
    for token in iter(self.control_queue.get, self.STOP_TOKEN):
      if token == self.TICK_TOKEN:
        self._new_cycle()
      self.control_queue.task_done()

    self.control_queue.task_done()
    logging.info('Terminating supervisor')

if __name__ == "__main__":
  s = Supervisor()
  print s._construct_targets()
