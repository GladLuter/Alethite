import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def manifest(*path):
    with open(os.path.join(ROOT, *path), encoding='utf-8') as f:
        return json.load(f)


class ManifestTest(unittest.TestCase):
    def test_plugin_and_marketplace_carry_the_new_name(self):
        plugin, market = manifest('plugin', '.claude-plugin', 'plugin.json'), manifest('.claude-plugin', 'marketplace.json')
        self.assertEqual(plugin['name'], 'alethite')
        self.assertRegex(plugin['version'], r'^\d+\.\d+\.\d+$')  # the number itself goes up before every merge into master
        self.assertTrue(plugin['description'].startswith('Alethite'))
        self.assertEqual((market['name'], market['plugins'][0]['name']), ('alethite', 'alethite'))
        self.assertTrue(market['description'].startswith('Alethite'))

    def test_agents_are_lean(self):
        want = {
            'coder': 'Read, Edit, Write, Bash, PowerShell, Grep, Glob',
            'reviewer': 'Read, Grep, Glob, Bash, PowerShell',
            'scout': 'Read, Grep, Glob',
            'researcher': 'Read, Grep, Glob, WebSearch, WebFetch',
            'resident': 'Read, Grep, Glob, Bash, PowerShell',
        }
        models = {'scout': 'haiku'}
        for name, tools in want.items():
            with open(os.path.join(ROOT, 'plugin', 'agents', name + '.md'), encoding='utf-8') as f:
                head = f.read().split('---')[1]
            fm = dict(l.split(':', 1) for l in head.strip().splitlines() if not l.startswith(' '))
            fm = {k.strip(): v.strip() for k, v in fm.items()}
            self.assertEqual(fm['name'], name)
            self.assertTrue(fm['description'])
            self.assertEqual(fm['tools'], tools)
            self.assertEqual(fm['model'], models.get(name, 'sonnet'))
            for bad in ('mcp__', 'Agent', 'Skill'):
                self.assertNotIn(bad, fm['tools'])
            self.assertEqual('omitClaudeMd' in fm and fm['omitClaudeMd'], 'true' if name == 'scout' else False)
            self.assertEqual('experimental' in fm, name == 'resident')
            self.assertEqual('cacheTtl: 1h' in head, name == 'resident')
            self.assertNotIn('effort', fm)
            if name == 'resident':
                self.assertIn('expensive', fm['description'])


if __name__ == '__main__':
    unittest.main()
