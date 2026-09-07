import base64
import io
import json
import urllib.error
from unittest.mock import patch
from PIL import Image
import test_cn_selection as B
import image_review as R
import mobile_layout as L
import merchandising as M

D=B.D


class FramingTests(B.SelectionTests):
    def test_auto_bbox_does_not_crop_original(self):
        jid=self.review();doc=D.read(jid);a=doc['assets'][0]
        a['product_bbox']=[.25,.25,.75,.75]
        photo=L.product_photo(doc,a,D.folder(jid),D.G,'low')
        with Image.open(D.folder(jid)/a['file']) as original:self.assertEqual(photo.size,original.size)

    def test_mixed_color_is_recolored_even_if_target_is_present(self):
        jid=self.review();doc=D.read(jid);a=doc['assets'][0]
        doc['color_request']='화이트,블랙';a['colors']=['화이트','분홍']
        L.product_photo(doc,a,D.folder(jid),D.G,'low')
        self.assertIn('원단 확대 부분',self.generate.call_args.args[0])
        self.assertIn('화이트',self.generate.call_args.args[0])

    def test_failed_visual_review_is_not_cached(self):
        jid=self.review();doc=D.read(jid);a=doc['assets'][0];a['has_overlay']=True
        with patch.object(R,'check',side_effect=R.ProductImageError('비율 오류')):
            with self.assertRaises(R.ProductImageError):L.product_photo(doc,a,D.folder(jid),D.G,'low')
        self.assertEqual(self.generate.call_count,2)
        self.assertEqual(list(D.folder(jid).glob('clean-product-*.jpg')),[])

    def test_long_detail_is_rewritten_before_rendering(self):
        jid=self.review();doc=D.read(jid)
        good={'hero':'움직임을 위한 탄탄한 파트너','details':[{'title':'다른 제목 '+str(i),'desc':'편안하게 감싸주는 제품의 장점을 느껴보세요.'} for i in range(3)]}
        bad=dict(good,details=[dict(p,desc='길게 반복하는 설명입니다. '*30) for p in good['details']])
        with patch.object(D.G.P,'_claude',side_effect=[json.dumps(bad),json.dumps(good)]) as api:
            result=M.section_copy(doc,D.G,D.folder(jid),lambda s:None)
        self.assertEqual(api.call_count,2)
        self.assertTrue(all(len(L.lines(p['desc'],750,32))<=3 for p in result['details']))

    def test_info_has_no_separate_color_strip(self):
        jid=self.review();doc=D.read(jid);doc['color_request']='화이트,블랙'
        doc['form']['product_info']=[{'label':'컬러','value':'화이트, 블랙'}]
        with patch.object(L,'lines',wraps=L.lines) as draw:
            L.product_info(doc,[doc['assets'][0]],D.folder(jid),'low',D.G)
        self.assertNotIn('화이트 · 블랙',[c.args[0] for c in draw.call_args_list])

    def test_safety_refusal_is_explained_without_raw_provider_text(self):
        error=urllib.error.HTTPError('https://api.openai.com',400,'bad',{},io.BytesIO(json.dumps({'error':{'code':'moderation_blocked','message':'safety_violations=[sexual]'}}).encode()))
        # Call the real transport, not this fixture's image-generation mock.
        self.patches[-1].stop()
        with patch.object(D.G.urllib.request,'urlopen',side_effect=error):
            with self.assertRaisesRegex(RuntimeError,'안전 검사') as caught:D.G._oai_image('adult sportswear')
        self.assertNotIn('sexual',str(caught.exception));error.close()
