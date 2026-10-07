"""Platform-specific copy and owned image exports. No invented sales claims."""
from pathlib import Path
from urllib.parse import urlsplit,parse_qsl,urlencode,urlunsplit
import re
CHANNELS=['pinterest','tiktok','instagram','youtube','amazon','gumroad']

def channel_link(campaign,channel):
    url=campaign['destination_url'];profile=campaign['profile']
    # Preserve affiliate links as approved; append UTMs only for an owned shop.
    if profile['mode']=='affiliate':return url
    p=urlsplit(url);q=dict(parse_qsl(p.query,keep_blank_values=True))
    q.update(utm_source=channel,utm_medium='organic',utm_campaign=campaign['id'])
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(q),p.fragment))

def kit(campaign,plan=None):
    product=campaign['product'];name=product['name']
    benefit=str(product.get('seller_benefit') or '').strip()
    body=(f'{name}: {benefit}' if benefit else f'A closer look at {name}. Check the product details, dimensions and current price before choosing.')
    disclosure='Affiliate link: I may earn a commission from qualifying purchases.' if campaign['profile']['mode']=='affiliate' else ''
    tags=['#'+x for x in re.findall(r'[A-Za-z]{3,}',product.get('category','Product Finds'))[:2]]
    result={}
    for channel in CHANNELS:
        link=channel_link(campaign,channel)
        cta='See our profile link for product details.' if channel in {'tiktok','instagram','youtube'} else 'View product details and current price.'
        instructions={
          'pinterest':'Upload pin.jpg, paste the title and description, and put the purchase link in the destination field. Choose your board.',
          'tiktok':'Upload the short video, paste the caption, and set the appropriate commercial-content disclosure. Put the purchase link in your profile when your account supports it.',
          'instagram':'Upload the short video as a Reel, paste the caption, and add the purchase link to your profile or an eligible Story link sticker.',
          'youtube':'Upload the short video in YouTube Studio. Add the purchase link to your channel profile. URLs in Shorts descriptions are not clickable.',
          'amazon':'Use the unbranded product-photo.jpg and listing-copy.txt only for a listing you own or are authorized to edit. Review category requirements. The social video is not an Amazon listing-video export.',
          'gumroad':'Use square.jpg as supporting product artwork, paste the product description, and upload the promotional video if appropriate. Your product listing and checkout must already exist.'
        }[channel]
        caption='\n\n'.join(x for x in [body,cta,disclosure,' '.join(tags) if channel!='amazon' else ''] if x)
        if channel=='amazon':caption='\n\n'.join(x for x in [name,benefit,'Confirm specifications against your exact listing before saving.'] if x)
        result[channel]=dict(title=name[:100],caption=caption,destination_url=link,instructions=instructions,
                             hashtags=tags if channel!='amazon' else [],state='not_posted',
                             video_available=channel!='amazon',publishing_mode='manual_upload',
                             disclosure=disclosure if channel!='amazon' else '',
                             suggested_angles=['Product overview','One supported feature','What to check before buying'])
    return result

def render_images(photo,directory,name,brand):
    from PIL import Image,ImageOps,ImageDraw,ImageFont
    import os,shutil
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    fontpath=os.environ.get('TRAFFICLIFT_FONT') or ('C:/Windows/Fonts/arial.ttf' if os.name=='nt' else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    with Image.open(photo) as src:
        if src.width*src.height>40_000_000:raise ValueError('Product photo is too large.')
        image=ImageOps.exif_transpose(src).convert('RGB')
        if min(image.size)<400:raise ValueError('Product photo is too small for this marketing kit.')
        image.save(directory/'product-photo.jpg',quality=95)
        for filename,size in [('pin.jpg',(1000,1500)),('square.jpg',(1080,1080)),('story.jpg',(1080,1920))]:
            width,height=size;canvas=Image.new('RGB',size,'#fbfaf7');d=ImageDraw.Draw(canvas)
            top=height-int(height*.32)
            margin=64
            fitted=ImageOps.contain(image,(width-margin*2,top-margin*2),Image.Resampling.LANCZOS)
            canvas.paste(fitted,((width-fitted.width)//2,(top-fitted.height)//2))
            d.rectangle((0,top,width,height),fill='#112c27')
            f=ImageFont.truetype(fontpath,42 if filename!='square.jpg' else 36)
            words=name.split();lines=['']
            for word in words:
                trial=(lines[-1]+' '+word).strip()
                if f.getlength(trial)>width-margin*2:
                    if lines[-1]:lines.append(word)
                    else:lines[-1]=word
                else:lines[-1]=trial
            lines=lines[:3]
            for i,line in enumerate(lines):
                while f.getlength(line)>width-margin*2 and line:line=line[:-1]
                if i==2 and len(words)>len(' '.join(lines).split()):line=line.rstrip()+'…'
                while f.getlength(line)>width-margin*2 and line:line=line[:-2]+'…'
                d.text((margin,top+38+i*52),line,font=f,fill='white')
            small=ImageFont.truetype(fontpath,26)
            d.text((margin,height-94),'See product details',font=small,fill='#bbecd3')
            b=brand[:45]
            while small.getlength(b)>width-margin*2:b=b[:-1]
            d.text((margin,height-48),b,font=small,fill='#bfcfca')
            canvas.save(directory/filename,quality=95)
