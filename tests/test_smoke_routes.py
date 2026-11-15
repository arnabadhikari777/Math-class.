# -*- coding: utf-8 -*-
"""Smoke test: every parameterless GET page answers without a server error (student + teacher)."""
import sys as _s, os as _o
_s.path.insert(0,_o.path.dirname(_o.path.abspath(__file__)))
from appcase import AppCase
class Smoke(AppCase):
    def test_all_get_routes_no_500(self):
        bad=[]
        for who in ("student","teacher"):
            cl=getattr(self,who)()
            for rule in self.app.url_map.iter_rules():
                if "GET" not in rule.methods or rule.arguments or rule.endpoint=="static": continue
                if "logout" in rule.rule: continue
                try: r=cl.get(rule.rule)
                except Exception as e: bad.append((who,rule.rule,repr(e)[:150])); continue
                if r.status_code>=500: bad.append((who,rule.rule,r.status_code))
        self.assertEqual(bad,[])
