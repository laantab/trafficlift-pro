from PIL import Image,ImageDraw
from backend.photo_quality import assess_photo,choose_photos,resolution_variants


def photo(path,size):
    im=Image.new('RGB',size,'white');d=ImageDraw.Draw(im)
    for x in range(0,size[0],30):d.rectangle((x,30,x+15,size[1]-30),fill='black')
    im.save(path)
    return path


def test_prefers_large_exact_photo_over_thumbnail(tmp_path):
    small=photo(tmp_path/'small.png',(400,400))
    large=photo(tmp_path/'large.png',(1400,1400))
    chosen=choose_photos([small,large])
    assert chosen[0][0]==large
    assert assess_photo(small)['status']=='NEEDS_REVIEW'
    assert chosen[0][1]['status']=='SOURCE_CHECKS_PASS'


def test_blank_image_is_excluded(tmp_path):
    blank=tmp_path/'blank.png';Image.new('RGB',(1400,1400),'white').save(blank)
    real=photo(tmp_path/'real.png',(900,900))
    assert [p for p,r in choose_photos([blank,real])]==[real]


def test_resolution_upgrade_keeps_exact_asset_path():
    url='https://target.scene7.com/is/image/Target/ABC123?wid=300'
    changed=resolution_variants(url)[0]
    assert '/Target/ABC123?' in changed and 'wid=1400' in changed
    amazon='https://m.media-amazon.com/images/I/ABC123._AC_SX300_.jpg'
    assert resolution_variants(amazon)[0]=='https://m.media-amazon.com/images/I/ABC123._SL1500_.jpg'
    assert resolution_variants('https://unknown.example/x.jpg')==['https://unknown.example/x.jpg']


def test_same_catalog_photo_at_two_resolutions_counts_as_one_verified_view(tmp_path):
    """One listing image returned at multiple URLs must not fake photo variety."""
    base=Image.new('RGB',(1400,1400),'white')
    draw=ImageDraw.Draw(base)
    draw.rounded_rectangle((310,160,1090,1190),radius=100,fill='#284F6A')
    draw.ellipse((500,400,900,850),fill='#D6A451')
    original=tmp_path/'catalog-original.jpg'
    base.save(original,quality=94)
    resized=tmp_path/'catalog-thumbnail.jpg'
    base.resize((800,800),Image.Resampling.LANCZOS).save(resized,quality=82)
    other=Image.new('RGB',(1200,1200),'white')
    d=ImageDraw.Draw(other)
    d.polygon([(160,250),(1080,220),(960,1060),(260,950)],fill='#284F6A')
    d.ellipse((420,340,860,800),fill='#D6A451')
    angle=tmp_path/'different-view.jpg'
    other.save(angle,quality=92)
    selected=[p for p,_ in choose_photos([resized,original,angle])]
    assert original in selected
    assert resized not in selected, 'Resolution variants must not count as separate product views'
    assert angle in selected, 'A genuinely different view must remain available'
    assert len(selected)==2


def test_duplicate_source_photo_cannot_fabricate_three_video_photos(tmp_path):
    base=Image.new('RGB',(900,900),'white')
    d=ImageDraw.Draw(base)
    d.rectangle((220,100,680,800),fill='#24495E')
    first=tmp_path/'one.jpg';base.save(first,quality=93)
    second=tmp_path/'same.jpg';base.save(second,quality=80)
    selected=choose_photos([first,second,first])
    assert len(selected)==1
