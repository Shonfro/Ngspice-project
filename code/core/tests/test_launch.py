"""Topology edge cases that differ between servers and scheduler allocations."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import launch

class TopologyTests(unittest.TestCase):
    def test_linux_cpu_list_with_gaps(self):
        self.assertEqual(launch.expand('16-19,48,50-51'),{16,17,18,19,48,50,51})
    def test_interleaved_smt_numbering(self):
        cpus,monitor=launch.choose_cpus(range(32),lambda cpu:(0,cpu//2))
        self.assertEqual(cpus,list(range(0,32,2)))
        self.assertEqual(monitor,1)
    def test_exactly_sixteen_cpu_allocation(self):
        cpus,monitor=launch.choose_cpus(range(16,32),lambda cpu:(0,cpu))
        self.assertEqual(len(cpus),16)
        self.assertIn(monitor,cpus)
    def test_small_allocation_is_rejected(self):
        with self.assertRaises(ValueError):launch.choose_cpus(range(8),lambda cpu:(0,cpu))
    def test_two_node_host_needs_no_node_two(self):
        nodes=[dict(node=0,allowed_cpus=list(range(16)),available_kib=3*1024**2),
               dict(node=1,allowed_cpus=list(range(16,32)),available_kib=4*1024**2)]
        self.assertEqual(launch.select_node(nodes,'auto')['node'],1)
        self.assertEqual(launch.select_node(nodes,'0')['node'],0)
    def test_explicit_bad_node_does_not_fall_back(self):
        with self.assertRaises(ValueError):launch.select_node([dict(node=1,allowed_cpus=list(range(32)),available_kib=4*1024**2)],'2')
    def test_memory_pressure_is_rejected(self):
        with self.assertRaises(ValueError):launch.select_node([dict(node=1,allowed_cpus=list(range(32)),available_kib=1000)],'auto')
    def test_reclaimable_memory_excludes_dirty_pages(self):
        self.assertEqual(launch.available_memory('Node 1 MemFree: 3000000 kB\nNode 1 Inactive(file): 1000000 kB\nNode 1 Dirty: 900 kB\nNode 1 Writeback: 100 kB\n'),3999000)

if __name__=='__main__':unittest.main()
