import base64
import io
import json
import unittest
from unittest.mock import patch, Mock

from PIL import Image
import test_cn_draft_editor as B
import source_photos as S
D, picture = B.D, B.picture


class SourcePhotoTests(unittest.TestCase):
    setUp = B.DraftEditorTests.setUp
    analysis = staticmethod(B.DraftEditorTests.analysis)
    create = B.DraftEditorTests.create
    run_task = B.DraftEditorTests.run_task
    def source_data(self):
        return {'title':'테스트 니삭스','images':[dict(url=f'https://img.alicdn.com/view-{i}.jpg',hint='',role='product') for i in range(15)]}

    def url_job(self, color_request='아이보리, 블랙', primary_color='아이보리', colors=3):
        body={'url':'https://www.cninsider.co.kr/mall/#/productinfo?myid=test',
              'images':[base64.b64encode(picture()).decode()] * 3,
              'color_request':color_request,'primary_color':primary_color,
              'color_images':[base64.b64encode(picture('pink')).decode()] * colors}
        jid=D.create(body)['id']
        def download(request, **kwargs):
            number=int(request.full_url.split('view-')[1].split('.')[0])
            return io.BytesIO(picture((number*15,100,120)))
        with patch.object(D.G,'login_and_scrape',return_value=self.source_data()), patch.object(S.urllib.request,'urlopen',side_effect=download):
            self.run_task()
        return jid

    def test_link_views_reach_generation_with_uploads_and_color_samples(self):
        jid=self.url_job()
        doc=D.read(jid)
        self.assertEqual(doc['status'],'done',doc.get('error'))
        self.assertEqual(doc['source_summary']['link'],15)
        self.assertEqual(doc['form']['option'],'아이보리, 블랙')
        self.assertEqual(doc['primary_color'],'아이보리')
        link_ids={a['id'] for a in doc['assets'] if a['origin']=='link'}
        for section, call in zip(doc['sections'],self.generate.call_args_list):
            self.assertTrue(link_ids.intersection(section['reference_ids']))
            self.assertEqual(len(call.kwargs['ref_imgs_b64']),15)
            self.assertIn('색상 외에',call.args[0])
            self.assertIn('색상 기준 캡처',call.args[0])
        self.assertIn('대표 색상 하나',self.generate.call_args_list[0].args[0])
        self.assertIn('판매 색상 전체',self.generate.call_args_list[8].args[0])
        self.assertNotIn('판매 색상 전체',self.generate.call_args_list[0].args[0])

    def test_high_keeps_current_image_and_all_source_and_color_roles(self):
        jid=self.url_job()
        raw=D.image_bytes(jid,0,'low')
        self.generate.reset_mock()
        D.action(jid,{'action':'high','indices':[0]});self.run_task()
        call=self.generate.call_args
        self.assertEqual(len(call.kwargs['ref_imgs_b64']),16)
        self.assertEqual(base64.b64decode(call.kwargs['ref_imgs_b64'][0][1]),raw)
        self.assertIn('첨부 2: 제품 참고',call.args[0])
        self.assertIn('첨부 16: 판매 색상 기준',call.args[0])

    def test_failed_downloads_do_not_shift_photo_identity(self):
        def download(request,**kwargs):
            if 'view-0' in request.full_url: raise OSError('not found')
            return io.BytesIO(picture('blue'))
        jid=D.create({'url':'https://www.cninsider.co.kr/mall/#/productinfo?myid=test'})['id']
        with patch.object(D.G,'login_and_scrape',return_value=self.source_data()),patch.object(S.urllib.request,'urlopen',side_effect=download):
            self.run_task()
        doc=D.read(jid)
        self.assertEqual(doc['status'],'done',doc.get('error'))
        self.assertIn('1개',doc['warning'])
        self.assertEqual(doc['source_summary']['link'],1)  # identical downloaded images deduplicated
        self.assertTrue(all((D.folder(jid)/a['file']).exists() for a in doc['assets']))
        self.assertTrue(all(s['reference_ids']==[doc['assets'][0]['id']] for s in doc['sections']))

    def test_no_link_images_stops_before_charging_even_with_uploads(self):
        jid=D.create({'url':'https://www.cninsider.co.kr/mall/#/productinfo?myid=test','images':[base64.b64encode(picture()).decode()]})['id']
        with patch.object(D.G,'login_and_scrape',return_value={'images':[]}):self.run_task()
        self.assertIn('링크 사진을 가져오지 못했습니다',D.status(jid)['error'])
        self.generate.assert_not_called()

    def test_color_samples_are_not_product_input_and_require_product_evidence(self):
        with self.assertRaises(D.DraftError):D.create({'color_images':[base64.b64encode(picture()).decode()]})
        with self.assertRaises(D.DraftError):self.url_job(colors=4)
        self.generate.assert_not_called()

    def test_unknown_primary_is_rejected_before_analysis_or_generation(self):
        with self.assertRaises(D.DraftError):self.url_job(primary_color='핑크')
        self.generate.assert_not_called()

    def test_target_color_save_changes_future_prompts_not_existing_images(self):
        jid=self.url_job()
        before=[s['low'] for s in D.status(jid)['sections']]
        self.generate.reset_mock()
        D.action(jid,{'action':'plan','form':{'color_request':'연핑크','primary_color':'연핑크'}})
        self.generate.assert_not_called()
        self.assertEqual([s['low'] for s in D.status(jid)['sections']],before)
        D.action(jid,{'action':'regenerate','indices':[7]});self.run_task()
        self.assertIn('판매 색상: 연핑크. 대표 색상: 연핑크',self.generate.call_args.args[0])

    def test_unrelated_products_are_never_sent_as_generation_references(self):
        jid=self.url_job()
        doc=D.read(jid)
        excluded=doc['assets'][4]
        excluded['usable']=False
        D.save(doc)
        D.action(jid,{'action':'regenerate','indices':[0]});self.run_task()
        self.assertNotIn(excluded['id'],D.read(jid)['sections'][0]['reference_ids'])

    def test_legacy_regeneration_collects_new_sources_preserving_existing_sections(self):
        jid=self.create()
        doc=D.read(jid);doc.pop('source_version');doc.pop('assets');D.save(doc)
        previous=doc['sections'][1]['low']
        D.action(jid,{'action':'regenerate','indices':[0]});self.run_task()
        updated=D.read(jid)
        self.assertEqual(updated['source_version'],2)
        self.assertEqual(updated['sections'][1]['low'],previous)

    def test_source_photo_endpoint_accepts_only_registered_ids(self):
        jid=self.url_job()
        doc=D.status(jid)
        self.assertNotIn('url',doc)
        self.assertTrue(all('file' not in a and 'url' not in a for a in doc['assets']))
        self.assertTrue(D.source_bytes(jid,doc['assets'][0]['id']).startswith(b'\xff\xd8'))
        with self.assertRaises(D.DraftError):D.source_bytes(jid,'../../cn.env')
        with self.assertRaises(D.DraftError):D.source_bytes(jid,'color-4')

    def test_candidate_selection_retains_detail_option_and_distinct_originals(self):
        rows=[dict(src='https://img.alicdn.com/a.jpg',w=800,h=800),
              dict(src='https://img.alicdn.com/a.jpg_200x200.jpg',w=200,h=200),
              dict(src='https://img.alicdn.com/long.jpg',w=750,h=4000),
              dict(src='https://img.alicdn.com/blue.jpg',w=200,h=200,hint='sku-option'),
              dict(src='https://img.alicdn.com/recommend.jpg',w=800,h=800,excluded=True),
              dict(src='https://alicdn.com.evil.test/a.jpg',w=800,h=800),
              dict(src='https://img.alicdn.com/a!!different.jpg',w=800,h=800)]
        images,total=S.image_candidates(rows)
        self.assertEqual(total,4)
        self.assertEqual({a['role'] for a in images},{'product','option','detail'})

    def test_long_detail_photos_are_readable_tiles_without_missing_bottom(self):
        image=Image.new('RGB',(500,3100),'red');image.paste('blue',(0,3000,500,3100))
        buffer=io.BytesIO();image.save(buffer,'JPEG')
        parts=list(S.photo_parts(buffer.getvalue()))
        self.assertEqual(len(parts),4)
        self.assertEqual(parts[-1][1:],(4,4))
        self.assertEqual(Image.open(io.BytesIO(parts[-1][0])).size,(500,100))

    def test_transport_sends_sixteen_images_without_silent_truncation(self):
        refs=[('image/jpeg',base64.b64encode(picture()).decode())]*16
        response=io.BytesIO(json.dumps({'data':[{'b64_json':refs[0][1]}]}).encode())
        with patch.object(D.G.urllib.request,'urlopen',return_value=response) as request:
            from importlib import util
            spec=util.spec_from_file_location('transport_for_test',D.G.__file__)
            module=util.module_from_spec(spec);spec.loader.exec_module(module)
            module._oai_image('test',ref_imgs_b64=refs,quality='low')
            body=request.call_args.args[0].data
            self.assertEqual(body.count(b'name="image[]"'),16)
            with self.assertRaises(ValueError):module._oai_image('test',ref_imgs_b64=refs+[refs[0]])

    def test_color_capture_only_is_resolved_without_using_capture_as_product(self):
        jid=self.url_job(color_request='',primary_color='',colors=1)
        doc=D.read(jid)
        self.assertEqual(doc['primary_color'],'흰색')
        self.assertEqual(doc['form']['option'],'흰색')
        self.assertTrue(all(not a['id'].startswith('color-') for a in doc['assets']))

    def test_section_plan_and_current_feature_choose_relevant_link_views(self):
        assets=[dict(id=str(i),origin='link',usable=True,role='detail',description='장식',view=str(i),colors=[],sections=[]) for i in range(20)]
        assets[-1]['description']='발바닥 논슬립 미끄럼 방지 패턴'
        doc=dict(assets=assets,form={'sellpoints':[{'title':'발바닥 논슬립','desc':'미끄럼 방지 패턴'}]})
        self.assertEqual(S.choose(doc,3)[0]['id'],'19')
        doc['photo_plan']={'7':['18','17']}
        self.assertEqual([a['id'] for a in S.choose(doc,7)[:2]],['18','17'])

    def test_homepage_login_and_other_product_are_not_successful_product_access(self):
        url='https://www.cninsider.co.kr/mall/#/productinfo?myid=ABC%3D'
        self.assertTrue(S.same_product_page(url,url.replace('%3D','=')))
        for route in ('homePage','login','productinfo?myid=OTHER','productinfo'):
            self.assertFalse(S.same_product_page(url,'https://www.cninsider.co.kr/mall/#/'+route))

    def test_late_home_redirect_reauthenticates_and_discards_homepage_candidates(self):
        url='https://www.cninsider.co.kr/mall/#/productinfo?myid=ABC'
        page=Mock();page.url=url
        page.goto.side_effect=lambda *args,**kwargs:setattr(page,'url',url)
        page.inner_text.return_value='같은 상품의 여러 색상과 여러 방향 사진을 담은 상품 상세 페이지'
        attempts=[]
        def scan(_):
            attempts.append(1)
            if len(attempts)==1:
                page.url='https://www.cninsider.co.kr/mall/#/homePage'
                return {'images':[{'url':'wrong','role':'product'}],'candidate_count':1}
            return {'images':[{'url':'right','role':'product'}],'candidate_count':1}
        login=Mock(return_value=True)
        with patch.object(S,'collect_page',side_effect=scan):
            result=S.open_product(page,url,login,Mock())
        self.assertEqual(result['main_imgs'],['right']);login.assert_called_once_with(page)

    def test_failed_source_refresh_preserves_legacy_results(self):
        jid=self.create();doc=D.read(jid);doc.pop('source_version');D.save(doc)
        old=doc['sections'][0]['low']
        with patch.object(S,'collect',side_effect=ValueError('링크 로그인 실패')):
            D.action(jid,{'action':'regenerate','indices':[0]});self.run_task()
        self.assertEqual(D.status(jid)['sections'][0]['low'],old)
        self.assertEqual(D.status(jid)['sections'][0]['status'],'error')

    def test_capture_palette_is_not_reduced_to_primary_when_saving_plan(self):
        jid=self.url_job(color_request='',primary_color='',colors=1)
        doc=D.read(jid);doc['form']['option']='흰색, 핑크';D.save(doc)
        D.action(jid,{'action':'plan','form':{'color_request':'','primary_color':'흰색','option':'흰색, 핑크'}})
        self.assertEqual(D.read(jid)['form']['option'],'흰색, 핑크')
        self.assertEqual(D.read(jid)['color_request'],'')
