import pytest
from backend import campaign_editor as editor
from backend.campaigns import HOLIDAY, plan, posts


def test_wrong_store_and_different_post_link_are_held():
    lines=plan(HOLIDAY,0,True)
    with pytest.raises(editor.EditorialHold):
        product=dict(HOLIDAY,destination="https://example.com/store")
        editor.check_copy(product,lines,posts(product,lines))
    drafts=posts(HOLIDAY,lines)
    drafts["linkedin"]+="?wrong"
    with pytest.raises(editor.EditorialHold):
        editor.check_copy(HOLIDAY,lines,drafts)


def test_receipt_matches_only_reviewed_bytes(tmp_path):
    for name in ["video.mp4","campaign.zip"]:
        (tmp_path/name).write_bytes(name.encode())
    assert not editor.verified(tmp_path,"kit")
    editor.receipt(tmp_path,375,4)
    assert editor.verified(tmp_path,"kit")
    (tmp_path/"campaign.zip").write_bytes(b"updated")
    assert not editor.verified(tmp_path,"kit")
