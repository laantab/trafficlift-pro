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
