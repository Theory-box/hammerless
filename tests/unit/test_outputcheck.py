import unittest
from hammerless.core.ir import Entity, Output
from hammerless.core.outputcheck import check_outputs


def entity(name, target=None, id=""):
    return Entity("logic_relay", keyvalues={"targetname":name}, imported_id=id,
                  outputs=[Output("OnTrigger", target, "Trigger")] if target else [])


class OutputCheckTests(unittest.TestCase):
    def test_wildcard_case_class_and_special_targets(self):
        es=[entity("fire_ballroom_01"), entity("source", "FIRE_ballroom*"),
            entity("a", "!activator"), entity("b", "LOGIC_RELAY")]
        self.assertEqual(list(check_outputs(es)), [])
        self.assertEqual(list(check_outputs([entity("a", "fire?")]))[0][0], "WARNING")

    def test_templates_do_not_hide_missing_members(self):
        es=[Entity("point_template",keyvalues={"Template01":"missing"}), entity("a","missing")]
        self.assertEqual(list(check_outputs(es))[0][0], "WARNING")
        es.append(entity("missing"))
        self.assertEqual(list(check_outputs(es)), [])

    def test_explicit_runtime_rename(self):
        es=[entity("before"),entity("caller","after"),entity("rename")]
        es[-1].outputs=[Output("OnTrigger","before","AddOutput","targetname after")]
        self.assertEqual(list(check_outputs(es)), [])
        es.pop(0)
        self.assertTrue(any(r[0]=="WARNING" for r in check_outputs(es)))

    def test_inherited_group_and_new_deleted_targets(self):
        old=[entity("source","old_unused","1"),entity("other","was_here","2"),entity("was_here",id="3")]
        current=old[:2]+[entity("new","old_unused","4")]
        rows=list(check_outputs(current, old))
        self.assertEqual([r[0] for r in rows], ["WARNING","WARNING","INFO"])
        self.assertIn("was_here",rows[0][1])
        self.assertIn("1 output references", rows[-1][1])
        changed=[entity("source","typo","1")]
        self.assertEqual(list(check_outputs(changed,old))[0][0],"WARNING")

    def test_import_baseline_reaches_build_and_check(self):
        from hammerless.core.vmfimport import Document, semantic_entities
        from hammerless.core.ir import MapIR
        from hammerless.core.build import validate, build_vmf
        from hammerless.core.vmf import VMFWriter
        doc=Document('entity { "id" "7" "classname" "logic_relay" connections { "OnTrigger" "old_target,Trigger,,0,-1" } }')
        current=semantic_entities(doc.entities, geometry=False)
        self.assertEqual(current[0].imported_id, "7")
        ir=MapIR(imported_entities=current, original_entities=semantic_entities(doc.entities, geometry=False))
        rep=validate(ir, physical=False)
        self.assertTrue(any(p.severity=="INFO" and "Original map:" in p.message for p in rep.problems))
        _, rep=build_vmf(MapIR(), base=(doc, doc.world, doc.entities, VMFWriter()))
        self.assertTrue(any("Original map:" in i for i in rep.info))
        self.assertFalse(any("old_target" in w for w in rep.warnings))
