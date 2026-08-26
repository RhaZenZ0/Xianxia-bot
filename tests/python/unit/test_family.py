import unittest
from app.family import sibling_title,relative_sibling_title

class PlayerFamilyTests(unittest.TestCase):
    def test_explicit_eldest_can_be_another_member(self):
        founder={"seniority_order":2,"address_style":"masculine"}; other={"seniority_order":1,"address_style":"masculine"}
        self.assertEqual(sibling_title(other),"Eldest Brother")
        self.assertEqual(relative_sibling_title(founder,other),"Eldest Brother")
    def test_gendered_family_titles_are_cosmetic(self):
        self.assertEqual(sibling_title({"seniority_order":2,"address_style":"feminine"}),"Second Sister")
        self.assertEqual(sibling_title({"seniority_order":2,"address_style":"neutral"}),"Second Sibling")

if __name__=="__main__": unittest.main()
