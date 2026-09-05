from __future__ import annotations

from typing import Any

_ORDINAL_WORDS = {1:"Eldest",2:"Second",3:"Third",4:"Fourth",5:"Fifth",6:"Sixth",7:"Seventh",8:"Eighth",9:"Ninth",10:"Tenth"}

def ordinal_word(position:int)->str:
    position=max(1,int(position)); return _ORDINAL_WORDS.get(position,f"No. {position}")

def sibling_title(member:dict[str,Any])->str:
    pos=max(1,int(member.get("seniority_order",1)))
    style=str(member.get("address_style") or "neutral").lower()
    noun="Brother" if style=="masculine" else "Sister" if style=="feminine" else "Sibling"
    return f"{ordinal_word(pos)} {noun}"

def relative_sibling_title(observer:dict[str,Any],target:dict[str,Any])->str:
    o=max(1,int(observer.get("seniority_order",1))); t=max(1,int(target.get("seniority_order",1)))
    style=str(target.get("address_style") or "neutral").lower()
    noun="Brother" if style=="masculine" else "Sister" if style=="feminine" else "Sibling"
    if t==1: return f"Eldest {noun}"
    if t<o: return f"Older {noun}"
    if t>o: return f"Younger {noun}"
    return sibling_title(target)
