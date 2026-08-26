import unittest

from app.sect import resolve_address


def person(uid, name, style="neutral", realm=0, phase=1, accepted=None):
    d = {
        "user_id": uid,
        "name": name,
        "address_style": style,
        "realm_index": realm,
        "phase": phase,
    }
    if accepted is not None:
        d["lineage_accepted_at"] = accepted
    return d


class SectAddressTests(unittest.TestCase):
    def setUp(self):
        self.observer = person(1, "Jian", "masculine", accepted=20)
        self.master = person(2, "Elder Yun", "masculine", accepted=10)
        self.grandmaster = person(3, "Patriarch Shen", "masculine")
        self.senior_brother = person(4, "Wei", "masculine")
        self.senior_brother["accepted_at"] = 15
        self.junior_sister = person(5, "Lan", "feminine")
        self.junior_sister["accepted_at"] = 25
        self.base = dict(
            observer_membership={"sect_name": "Azure Cloud Sect", "rank_level": 1},
            target_membership={"sect_name": "Azure Cloud Sect", "rank_level": 1},
            observer_master=self.master,
            target_master=None,
            observer_grandmaster=self.grandmaster,
            observer_master_master=self.grandmaster,
            sibling_rows=[self.senior_brother, self.junior_sister],
            master_sibling_rows=[],
        )

    def test_master_english_default(self):
        r = resolve_address(self.observer, self.master, **self.base)
        self.assertEqual(r.translation, "Master")
        self.assertNotIn("Shifu", r.display)
        self.assertIn("Master", r.display)
        self.assertIn("Shifu", r.display_chinese)

    def test_senior_brother(self):
        r = resolve_address(self.observer, self.senior_brother, **self.base)
        self.assertEqual(r.translation, "Senior Brother")

    def test_junior_sister(self):
        r = resolve_address(self.observer, self.junior_sister, **self.base)
        self.assertEqual(r.translation, "Junior Sister")


if __name__ == "__main__":
    unittest.main()
