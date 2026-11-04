# 태그 분류 지침 (LLM·사람 공통)

general 태그 하나에 아래 7개 값을 정한다. 이 문서는 LLM 배치 분류의 지시문이기도 하다.
카테고리 정의는 [data/taxonomy.json](../data/taxonomy.json)의 `desc`가 정본이고, 여기에는 판단 기준과 경계 사례를 적는다.

| 필드 | 값 | 비우면 |
|---|---|---|
| `primary` | `main/sub` 1개 (서브를 정하기 어려우면 `main`만) | 안 됨 |
| `secondary` | `main/sub` 0~2개, `\|`로 구분 | 없음 |
| `rating` | g / s / q / e | 등급 판단 대상이 아닌 카테고리(meta·style·subject·series·text·effect·background) |
| `regions` | 부위 0~4개, `\|`로 구분 | 구도 규칙으로 빼지 않음 |
| `identity` | 1 = 캐릭터 고유 외형 | 0 |
| `confidence` | 0.5~1.0 | – |
| `note` | 판단 근거가 애매할 때 한 줄 | – |

## 1. primary 고르기

**원칙: 그 태그가 그림에서 "무엇을 그리게 하는가"로 고른다.** 단어의 문법적 성격(형용사·명사)이 아니라 그려지는 대상이 기준이다.

- 여러 곳에 걸치면 프롬프트를 정리할 때 사람이 찾을 자리를 primary로, 나머지를 secondary로 둔다.
  - `tears` → primary `object/fluid`, secondary `action/expression`
  - `wet` → primary `body/skin`, secondary `clothes/state`
- **의상 한 벌**(shirt, skirt, dress)과 **의상의 디테일**(frills, buttons, fur trim, striped clothes, clothing cutout)을 구분한다. 디테일은 `clothes/detail`.
- **노출 상태**(bare shoulders, midriff, barefoot, nude, open clothes)는 옷이 없거나 열린 상태이므로 `clothes/state`. 신체 부위 이름 자체(navel, collarbone, thighs)는 `body`.
- **역할·직업 의상**(maid, miko, nun, witch)은 `clothes` 메인만 쓴다(서브 없이). 머리 장식처럼 부분이면 해당 서브.
- **alternate costume, cosplay**처럼 "평소와 다른 옷"이라는 정보는 `clothes` 메인만.
- **holding X**는 `action/holding`, 들고 있는 물건 자체(sword, cup)는 `object`.
- **looking X**는 `action/gaze`. **facing X**는 위키 정의상 looking X에서 눈을 감거나 가린 짝이므로 같은 `action/gaze`(facing viewer, facing away, facing back, facing to the side, facing down). 보는 쪽이 아니라 카메라 위치를 말하는 태그(from side, from behind, profile, straight-on)만 `composition/angle`. 두 인물이 몸을 마주 대는 것(facing each other)은 back-to-back처럼 `action/interaction`.
- **활동**(eating, reading, sleeping, walking, running, singing, fighting)은 `action/activity`. 몸 자세(standing, sitting, lying, kneeling, on back, on bed)는 `action/posture`. 손·팔·다리 동작(hand up, v, arms behind back, crossed legs, spread legs, head tilt)은 `action/gesture`.
- **표정**은 얼굴에 드러나는 것 전부: 웃음·입 모양·눈 감기·볼 붉힘·눈썹(v-shaped eyebrows)·이모티콘 태그(:d, ^_^, :3) → `action/expression`.
- **만화 기호**(heart, star (symbol), sweatdrop, flying sweatdrops, anger vein, musical note) → `object/symbol`.
- **가구·창문·침대·의자 등 배경에 놓인 큰 물체** → `background/scenery`. 손에 들 수 있는 물건 → `object/prop`.
- **배경 색·형식**(simple/white/gradient/transparent background) → `background/bg_type`. 장소(indoors, outdoors, beach, classroom) → `background/location`. 시간·날씨·계절·행사(day, night, rain, autumn, christmas) → `background/time`.
- **빛·대기·렌즈**: 빛과 그림자(shadow, sunlight, backlighting, glowing) → `effect/lighting`. 공중의 입자·연기·불·마법(sparkle, light particles, steam, fire, magic) → `effect/atmosphere`. 흐림·초점·렌즈(blurry, depth of field, bokeh, lens flare, chromatic aberration) → `effect/lens`. 움직임 선(motion lines, speed lines) → `effect/motion`.
- **그림 자체의 처리**(monochrome, greyscale, spot color) → `style/color`. 화풍(sketch, chibi, realistic, anime coloring) → `style/art_style`.
- **글자·서명**: 작가가 넣은 서명·워터마크·날짜·SNS 아이디·이름 표기 → `text/mark`. 그림 속 글자·말풍선 → `text/in_image`.
- **화면 형식**(comic, 4koma, multiple views, border, white border) → `composition/format`. 크롭은 `framing`, 시점은 `angle`, `X focus`는 `focus`.
- **인원 수**(1girl, 2boys, solo, multiple girls, no humans, 1other) → `subject/count`. 관계(siblings, sisters, couple) → `subject` 메인만. 종족·존재 유형(cat girl, fox girl, furry, demon girl, horse girl, robot, elf) → `subject/species`.
- **동물 귀·꼬리·날개·뿔·후광**(animal ears, tail, wings, horns, halo, pointy ears) → `body/features`. 머리띠형 가짜 귀(fake animal ears)는 `clothes/hair_acc`.
- **검열**(censored, mosaic censoring, bar censor, uncensored) → `meta/technical` (등급만 매긴다).
- **작품 성격·장르**(fantasy, science fiction, contemporary, parody, crossover, meme, personification, bara) → `meta/genre`. 행사·시기(christmas, halloween, valentine)는 `background/time`.
- **탈것·큰 기계**(car, motor vehicle, aircraft, mecha) → `object/vehicle`.
- **나이·체형 인상**(child, mature female, petite, loli, shota, androgynous) → `body/skin`, identity 1. 일시적 변형(aged down, aged up, alternate breast size)은 identity 0.
- **관계**(couple, siblings, twins, age difference, height difference) → `subject` 메인만. 성별·캐릭터 변형(genderswap)도 `subject` 메인, identity 0.
- **안 입은 옷**(unworn hat, missing headwear 등)은 `clothes/state`. 그것을 손에 들고 있으면(holding unworn clothes) `action/holding`.
- **입에 문 것**(mouth hold, food in mouth)은 `action/holding`, regions face.
- **그리기 관례**(outline, silhouette, halftone, chibi only)는 `style/art_style`. 매체(oekaki, card (medium), dakimakura (medium))는 `style/medium`.
- **가린 것 너머로 보이게 그린 부위**(eyes visible through hair, eyebrows visible through hat, blush visible through hair, mouth visible through hair)는 그림체가 아니라 **최종으로 보이는 그 부위의 분류**로 한다: 눈 → `body/eyes`, 눈썹·입·수염 → `body/face`, 귀 → `body/features`, 홍조 → `action/expression`(blush 와 같이), 안경 → `clothes/accessory`. regions 는 그 부위(eyes·face·head), rating 은 g. 그림체로 두면 배열에서 작가·화풍 줄에 섞인다.
- **크롭으로 잘린 부위**(cropped legs, cropped torso, foot out of frame, out of frame, wide shot, close-up)는 `composition/framing`. 원근(foreshortening, pov hands)은 `composition/angle`.

## 2. rating (그 태그가 있으면 이미지가 최소 이 등급이 된다)

| 등급 | 기준 | 예 |
|---|---|---|
| g | 일반. 평범한 옷·수영복, 성적이지 않은 신체 부위 | swimsuit, navel, thighs, large breasts, armpits, midriff |
| s | 성적 강조나 큰 노출, 속옷 노출 | cleavage, bikini, panties, bra, underboob, sideboob, covered nipples, no bra, clothes lift, cameltoe, spread legs, groin |
| q | 부분 나체(성기 제외) | nipples, nude, completely nude, bottomless, topless, pubic hair, groping, censored |
| e | 성기, 성행위, 체액 | penis, pussy, anus, testicles, erection, sex, vaginal, oral, cum, pussy juice, uncensored |

경계 사례: 성적 맥락에서 주로 쓰이지만 태그 자체는 장면을 확정하지 않는 것(hetero, yuri, yaoi, couple, interracial)은 등급을 비우거나 g로 둔다. 자세 이름이 곧 성행위인 것(missionary, cowgirl position, doggystyle)은 e. 속옷이나 성기 노출을 함축하는 것(no panties, panties aside, covering privates)은 s~q. 성인용품(vibrator, dildo, condom)은 q.

- 등급 판단 대상 카테고리(composition, action, clothes, body, object)는 반드시 채운다. 나머지는 비운다. 단, 검열 태그처럼 성적 내용을 함축하면 어느 카테고리든 채운다.
- 애매하면 낮은 쪽을 고르고 confidence를 낮춘다. 필터가 과하게 지우는 것보다 덜 지우는 편이 낫다.

## 3. regions (구도 규칙용 신체 부위)

부위 어휘: `head, face, eyes, front_torso, back, arms, hands, hips, front_hips, back_hips, thighs, lower_legs, back_legs, feet`

**태그의 내용이 그 부위가 화면에 없으면 그려질 수 없을 때만** 붙인다. 붙인 부위가 **전부** 가려질 때만 태그가 빠진다. 확신이 없으면 비운다. 잘못 지우는 것이 놓치는 것보다 나쁘다.

| 대상 | regions |
|---|---|
| 머리카락·머리 장식·모자·동물 귀·뿔·후광·귀걸이 | head |
| 표정·입·볼·얼굴 특징·안경·마스크 | face |
| 눈 색·눈 모양·눈 감기 | eyes |
| 가슴·쇄골·배꼽·배·가슴 노출·넥타이·리본 타이 | front_torso |
| 상의·원피스 상반신 | front_torso\|back |
| 원피스 전체(dress) | front_torso\|back\|hips\|thighs |
| 등(back) | back |
| 소매·팔 노출·겨드랑이·팔꿈치 장갑 | arms (장갑은 arms\|hands) |
| 장갑·손톱·반지·손 제스처(v, hand up) | hands |
| 골반 앞면에만 있는 것: 사타구니·치골·음모·버클 | front_hips |
| 골반 뒷면에만 있는 것: 엉덩이·엉덩이 골·항문 | back_hips |
| 앞뒤에서 모두 보이는 것: 골반 폭·벨트·속옷 하의·성기·삽입과 체위 | hips |
| 치마·반바지 | hips\|thighs |
| 바지 | hips\|thighs\|lower_legs |
| 허벅지·절대영역·허벅지 끈 | thighs |
| 다리 전체·니삭스(thighhighs) | thighs\|lower_legs |
| 다리 뒷면에만 있는 것: 무릎 뒤 | back_legs |
| 팬티스타킹 | hips\|thighs\|lower_legs\|feet |
| 양말·무릎 양말 | lower_legs\|feet |
| 부츠 | lower_legs\|feet |
| 신발·맨발·발·발가락·발바닥 | feet |

비우는 것: 꼬리·날개(정면·측면에서도 보임), 자세·활동·시선, holding X, 목에 거는 것(choker, collar, scarf), 몸 전체에 걸친 상태(wet, sweat, shiny skin), 배경·이펙트·메타.

앞뒤는 서 있는 자세에서 반대쪽으로 보이지 않을 때만 나눈다. 확신이 없으면 hips로 둔다.

참고: from behind는 front_torso·front_hips·face·eyes를, 정면 쪽 시점(straight-on, front view, three quarter view)은 back·back_hips·back_legs를 가린다. from side는 아무것도 가리지 않는다. 그래서 날개·꼬리처럼 등에 붙어도 정면에서 보이는 것에는 back을 붙이지 않는다. 고개를 돌려 얼굴이 보이는 시선 태그(looking 계열, facing viewer·back·to the side·up)가 있으면 face·eyes는 보인다. 따라서 표정 태그는 from behind 단일 샷에서 시선 태그가 없거나 facing away일 때 빠진다.

## 4. identity (캐릭터 고유 외형)

"다른 캐릭터로 바꿔 그리면 바뀌어야 하는 외형"이면 1.

- 1: 머리 색·길이·모양(twintails, ponytail, bangs 종류, ahoge, sidelocks, braid), 눈 색·눈 모양(heterochromia, slit pupils, tsurime), 피부색(dark skin), 체형(breasts 크기, flat chest, muscular, abs, thick thighs, curvy), 얼굴 특징(freckles, mole under eye, fang, thick eyebrows, facial hair), 흉터·문신, 동물 귀·꼬리·날개·뿔·후광, 종족(subject/species)
- 0: 상태·순간적인 것(floating hair, messy hair, wet hair, alternate hairstyle, glowing eyes, heart-shaped pupils, body blush, shiny skin), 화장·매니큐어, 신체 부위 이름(navel, collarbone, thighs, feet)
- 의상·장신구는 identity를 쓰지 않는다(의상은 `clothes` 선택자로 따로 뺀다).

## 5. confidence

- 0.9~1.0: 의미가 분명하고 위 기준에 바로 들어맞음
- 0.7~0.8: 두 카테고리 사이에서 골랐음
- 0.5~0.6: 의미가 불확실하거나 기준이 없음 → note에 이유

## 6. 보강 규칙

아래 규칙이 더 구체적이면 이 절을 따른다.

- **선례 우선**: 애매하면 `data/overlay/tags.csv`에서 같은 기본 단어·접미사·계열 태그를 찾아 같은 방식으로 맞춘다.
- **수식어**: 색·무늬 수식어(white_, striped_, plaid_, frilled_ 등)는 기본 아이템과 같은 primary·rating·regions. 상태 수식어(torn_, open_, see-through_, unworn_)는 `clothes/state`. `X_under_Y`는 X의 부위를 따른다. `*_pull`(옷을 당김)은 s.
- **노출 변형**: 노출을 키우는 변형(o-ring top, micro shorts, latex 계열)은 기본 아이템보다 등급을 한 단계 올리되, 성기·젖꼭지 노출을 함축하지 않으면 s를 넘지 않는다(micro bikini, pearl thong은 s).
- **사물과 음식**: 먹고 마시는 것만 `object/food`. 컵·빨대·그릇·병·포장 같은 도구와 용기는 `object/prop`.
- **위에 놓인 것**: on_head·on_shoulder·on_lap·on_person처럼 "무언가가 ~ 위에 놓임" 태그는 `object` 메인. 동물이 특정되면(rabbit_on_head, bird_on_head, animal_around_neck) `object/animal`. 사람이 ~ 위에 있는 자세(on_bed, on_chair, sitting_on_*)는 `action/posture`·`action/interaction`.
- **눈물**: 눈물 계열(tears, streaming tears, happy tears)은 `object/fluid` + secondary `action/expression`. 우는 동작·표정(crying, tearing up)은 `action/expression`.
- **피어싱**: 위치와 관계없이 `clothes/accessory`(등급은 위치에 따라 — 젖꼭지는 q).
- **동물 신체 부위**: animal nose, snout, beak, pawpads, claws, 동물 귀·꼬리·뿔은 `body/features`, identity 1.
- **피부 표식**: 점·흉터·문신·멍·주근깨는 얼굴이면 `body/face`, 그 밖에는 위치와 관계없이 `body/skin`. 위치는 regions로 적는다.
- **선**: 만화 기호 선(notice lines, sweatdrop, anger vein)은 `object/symbol`. 움직임 선(speed lines, motion lines, emphasis lines)은 `effect/motion`.
- **검열**: 성기·젖꼭지를 가리는 검열(mosaic, bar, heart, novelty, blur, scribble, light)은 q. 실제로 드러난 것이 없는 가림(steam censor, convenient censoring)은 s.
- **체액**: 체액 태그는 옷 위에 있어도 기본 체액과 같은 등급(lactation through clothes는 q).
- **peek 계열**: 옷 틈으로 성기·항문이 살짝 보이는 `*_peek`는 q.
- **잔혹 묘사**: impaled, intestines, decapitation, severed limb 같은 잔혹 묘사는 q. 장르 태그 guro는 e.
- **설정·변형**: 장면 재현·패러디·변형 설정(scene reference, kemonomimi mode, humanization, animalization, body switch)은 `meta/genre`. 성별 변형(genderswap 계열)과 원본·변형을 함께 그린 짝 태그(person and animalization, creature and personification)는 `subject` 메인.
- **그룹·유닛·세력**: 작품 속 아이돌·밴드 유닛과 그룹 이름은 `series`(캐릭터 교체 때 함께 빠지도록). 세력의 제복을 뜻하는 이름(zeon, team rocket)은 `clothes` 메인.
- **글자**: 그림 속 글자에 대한 태그(typo, engrish text, romaji text)는 `text/in_image`. 사이트·제작 관리 태그(username, logo, watermark)는 `text/mark`, 작품 연결 태그(*_connection)는 `meta/technical`, 연도 태그(2019 등)는 `meta/date`.
- **교복·제복**: 작품별 교복·제복 이름은 `clothes/onepiece`.

## 7. 드문 태그와 분류 보류(etc)

general 상위 10,000위 밖(사용 20~960회)의 태그를 위한 규칙이다. **뜻이 불확실한 태그는 추측하지 말고 미분류(etc)로 남긴다.**

- **보류(etc)할 때**: 이름·별칭·한정어 `(…)`로도 그림에 무엇이 그려지는지 알 수 없을 때. 작품 속 고유명사·밈·말장난·약어처럼 뜻을 모르는 것, 또는 confidence 0.6 이하로밖에 정할 수 없는 것.
  - 형식: `primary` = `etc`, `secondary`·`rating`·`regions`·`identity`·`confidence`는 비우고, `note`에 `의미 불확실`(짐작이 있으면 `의미 불확실 · 아이마스 의상 추정`처럼 덧붙임).
  - 런타임에서는 분류하지 않은 태그와 똑같이 동작한다(지우지 않고 미분류 목록에 남음). 사용자가 나중에 손으로 분류할 수 있게 표시만 해 두는 것이다.
- **보류하지 않을 때**: 구조로 뜻이 정해지는 태그는 처음 보는 단어가 섞여도 분류한다. holding_X → `action/holding`, X_print → 무늬, 색·무늬 수식어 + 아는 아이템, X_hair_ornament → `clothes/hair_acc`, X_on_head → `object` 등.
  - 실존하는 사물·동식물·장소·탈것·총기 모델명(clarinet, pomegranate, m14, chevrolet corvette c3)은 아는 것이면 분류한다.
- **한정어 계열**(선례가 있는 기본값):
  - `X_(cosplay)` → `clothes` 메인(서브 없이), 등급은 그 캐릭터 의상의 노출 정도(대개 g, 노출이 큰 의상은 s).
  - `X_(style)` → `style/art_style`(작가·시대 화풍 모방).
  - `X_(meme)` → `meta/genre`. 성적 함축이 있으면 등급을 채운다.
  - `X_(phrase)` → `text/in_image`.
  - 작품 한정어가 붙은 의상 이름(교복·제복·무대 의상)은 `clothes/onepiece` 또는 `clothes`, 무기·소품 이름은 `object/weapon`·`object/prop`. 그것이 의상인지 곡·이벤트·기술인지 모르면 보류.
- **경계 사례**
  - 무엇의 **종류**인지만 알면 분류한다. 정확한 정체는 몰라도 된다(p416 → 총기이므로 `object/weapon`).
  - 공연·투어·게임 이벤트·게임 루트 이름은 그리게 하는 것(의상·장면)을 모르면 보류. 작품 속 장소 이름은 `background/location`, 곡·오프닝 장면 재현은 `meta/genre`, 작품 속 방송·코너 이름은 `series`.
  - `X_between_breasts`는 X를 따르고 s(arm → `action/gesture`, necktie → `clothes/state`, microphone → `object/prop`).
  - 활동(`action/activity`)과 상호작용(`action/interaction`)은 regions를 비운다. 손·발로 하는 몸짓(`action/gesture`)만 hands·feet.
- **증거로 다시 분류할 때**: 위 보류는 이름만 보고 정한 것이다. 배치에 `wiki`·`other_names`·`implies`·`related`·`related_series` 칸이 있으면 Danbooru에서 받은 증거다(`tagorg evidence`). `?`는 받지 못한 것, 빈 칸은 받았는데 없는 것이다.
  - **위키 정의가 1순위**다. 위키가 없거나 그려지는 것을 말하지 않으면 **연관 태그**를 본다. `related`의 비율은 이 태그가 붙은 게시물 중 그 태그도 붙은 비율이고, 1girl·solo·smile 같은 공통 태그는 미리 뺐다. 0.5 이상으로 일관되게 붙는 의상·사물·장면 태그가 근거가 된다. `implies`(함의)는 상위 태그이므로 그 분류를 따를 근거다.
  - **이벤트·카드 세트·라이브·기획 이름**: 연관 태그에 `official_alternate_costume`이 0.7 이상이거나 특정 의상 태그(ao_dai, wa_maid, hawaiian_shirt 등)가 0.7 이상이면 그 이벤트 카드의 의상으로 보고 `clothes` 메인(서브 없이)으로 한다. 등급은 그 의상의 노출 정도를 따른다(대개 g, 비키니가 중심이면 s).
    - 의상 근거는 없지만 장면·사물이 일관되면 그것으로 분류한다(onsen 0.6 → `background/location`, still_life·food → `object/food`). 둘 다 없으면 보류.
    - 0.7은 글자 그대로 적용한다(0.69는 미달, 다른 태그와 합산하지 않음). hat·gloves·dress·frills·shirt·jacket 같은 일반 의류는 "특정 의상 태그"가 아니다.
    - 위키가 **태그 자체를 의상으로 정의**하면(공식 의상 이름, "X 콜라보 의상을 입은 캐릭터", 카드마다 의상 이름을 적은 목록 — Twisted Wonderland의 "(Tropical Wear)") 0.7과 관계없이 `clothes`다. 이벤트를 "수영복 이벤트"라고 부르는 이름·별칭이나 줄거리만으로는 부족하다(FGO 여름 이벤트는 swimsuit·bikini 0.7 이상일 때만).
  - 위키 정의와 실제 쓰임(연관 태그·작품)이 어긋나면 보류하고 note에 적는다(bois_de_justice: 위키는 Dies Irae의 무기인데 게시물은 cookie_(touhou)).
  - 위키가 스스로 "Ambiguous tag"(뜻이 여럿이니 다른 태그를 쓰라)라고 하면 보류.
  - 실재하는 주제·개념 태그(true_crime, irony, generation_gap)는 `meta/genre`.
  - 그룹·유닛·스쿼드 이름은 §6대로 `series`. 연관 태그로 그 유닛 의상에도 쓰이는 것이 보이면 secondary `clothes`.
  - note에는 근거를 짧게 적는다(`위키: 러시아식 체스트 리그`, `연관: pencil_skirt 0.80·necktie 0.61 → 의상`).

## 8. 확장 DB 태그 (data/extra)

원본 CSV 에 없는 태그(`data/extra/<DB>.csv`)다. 칸과 규칙은 §1~§7 과 같고, 아래가 다르다. 배치는 `batch export --db <DB>` 로 만들고 결과는 `batch apply --db <DB>` 로 넣는다. 결과 CSV 에는 `use_instead` 칸을 더 둘 수 있다.

- **Danbooru 폐기 태그(`deprecated`)**: Danbooru 가 더는 쓰지 않아 원본에서 빠진 태그다. 옛 데이터로 학습한 모델이 알고, 사용자가 프롬프트에 일부러 쓴다. 증거 칸(§7)이 있고, `count` 는 지금 Danbooru 게시물 수다(대부분 0 — 폐기 뒤 다른 태그로 옮겨졌기 때문이며 뜻이 없다는 뜻이 아니다). 0회 태그는 연관 태그가 없으므로(빈 칸) 위키와 이름으로 판단한다.
  - 위키는 대개 폐기 이유와 대신 쓸 태그를 말한다("Use X instead", "Deprecated. See X"). **이름이 가리키는 것이 분명하면 그대로 분류**하고, 대신 쓸 태그가 있으면 그 태그의 overlay 분류(선례)를 따른다. 대신 쓸 태그는 `use_instead` 에 Danbooru 표기로 적는다(여럿이면 `|`). 예: aqua_legwear → `clothes/legwear`, use_instead `aqua_thighhighs|aqua_pantyhose`.
  - **캐릭터에게 거는 주관적 인상·평가 형용사**(cute, adorable, kawaii, cool, silly, wholesome, playful, pretty, beautiful, gorgeous, handsome, attractive — "주관적이라 폐기")는 캐릭터의 분위기·태도를 바라는 말이므로 `action/expression`, 등급 g, regions 는 비운다(얼굴이 안 보여도 자세·분위기로 드러남). 얼굴까지 바꾸는 것(cute·adorable·kawaii → 동안·부드러운 표정)은 secondary `body/face`. 성적 함축이 있으면 등급을 채운다(sexy, slutty → s). 몸매 평가(perfect body)는 `body/skin`, identity 0(특정 체형이 아님). 그림 전체에 대한 평가(awesome, epic)와 모델 미학·복잡도 태그만 `meta/aesthetic` 이다. 배열에서 표정 줄(smile·blush 옆)에 놓이게 하려는 것이다.
  - 위키가 "Ambiguous tag"라고 해도 **제시한 후보들이 한 분류로 모이면** 그 분류로 하고 후보를 `use_instead` 에 적는다(peaceful → happy·light smile → `action/expression`). 후보가 서로 다른 분류로 갈리면(notes → musical note·notebook·papers) 보류(etc)하고 후보는 `use_instead` 에 적는다. 이 점은 §7 의 "Ambiguous tag 는 보류"보다 우선한다.
  - 동명이인이 많아 폐기된 **사람 이름만 있는 태그**(claire, touya, akizuki — 위키가 작가·캐릭터 목록만 줌)와 작품 이름 조각은 보류. use_instead 는 비운다(후보가 너무 많음).
  - 위키가 없거나 그려지는 것을 말하지 않고(See topic #…) 이름으로도 모르면 §7 대로 보류.
  - 일반 낱말 뜻 하나와 동명 캐릭터·작가가 겹치는 태그(raven, sabre, lily)는 일반 뜻으로 분류(confidence 0.7). 일본어·중국어 로마자 낱말(kamibukuro, kabocha)은 프롬프트에서 그 뜻으로 쓰이기 어려우므로 보류하고 위키가 가리킨 일반 태그를 `use_instead` 에 적는다.
  - 자기·남의 것을 가리지 않는 손 동작: `hand(s)_on_X`·`biting_X`·`X_hold`·`holding_X`(신체 부위)는 `action/gesture` + secondary `action/interaction`, `X_grab`·`grabbing` 은 `action/interaction` + secondary `action/gesture`. 성적 부위면 그 쪽을 secondary `action/sexual` 로. 등급은 두 뜻 중 낮은 쪽.
  - 등급을 뜻하는 말(nsfw, ecchi, lewd, hentai, erotic, r18, sfw)은 `meta/rating_tag` 이고 등급 칸은 비운다. 화면비·파일 형식·파일 크기·영상 길이 검색 태그(16:9, png, long_image)는 `meta/technical`.
- **내 태그(`user`)**: 사용자가 쓰는 커뮤니티 프롬프트 문구다(cinematic lighting, teal and orange color grading, award-winning artwork). Danbooru 증거는 없고 영어 뜻 그대로 분류한다. 사용자가 일부러 쓰는 말이므로 뜻을 알면 보류하지 않는다.
  - 조명·빛 → `effect/lighting`, 안개·빛 번짐·입자·장면 분위기(ethereal atmosphere, serene mood, cozy ambiance) → `effect/atmosphere`, 렌즈·피사계 심도·플레어 → `effect/lens`.
  - 색감·색 보정·팔레트 → `style/color`, 렌더링·붓질·선 처리·음영 방식 → `style/art_style`, 매체(digital painting) → `style/medium`.
  - 구도·화각·촬영 기법 → `composition`(시점은 `angle`, 크롭은 `framing`, 그 밖에는 서브 없이 `composition`).
  - 품질·정밀도 찬사(highly detailed, intricate details, sharp focus, award-winning artwork, professional illustration) → `meta/quality`.
  - 배경을 풍부하게 하라는 문구(lush scenery, detailed backdrop) → `background/scenery`.
  - 신체·질감 묘사(large eyes, lustrous skin, realistic skin texture)는 해당 `body` 서브, identity 0.

## 9. e621 태그 (두 번째 원본)

e621 에만 있는 태그(`data/overlay/e621.csv`)다. Danbooru 와 같은 이름은 `tags.csv` 한 행을 함께 쓰므로 여기서 다루지 않는다([docs/e621.md](e621.md)). 칸과 규칙은 §1~§7 과 같고, 아래가 다르다. 배치는 `e621 batch export`, 결과는 `e621 batch apply` 로 넣는다.

- **배치 칸**: `kind`(왜 골랐나 — 아래), `type`(e621 종류: general·meta), `count`(e621 게시물 수), `aliases`(e621 별칭), `current`(지금 팩의 분류와 상태·메모), `danbooru`(Danbooru 쪽 단서 — Danbooru 별칭이면 `별칭 대상 X=분류`, e621 별칭 중 Danbooru 태그 이름이 있으면 `X=분류`), `wiki`·`implies`·`implied_by`(e621 위키와 함의 — e621 DB 내보내기 사본, `?` 는 자료 없음, 빈 칸은 없음). `implies` 는 상위 태그, `implied_by` 는 이 태그를 함의하는 하위 태그(사용량 순)라 뜻의 범위를 보여 준다.
- **원칙: e621 사용자가 그 태그로 그리게 하는 것**으로 분류한다(e621 위키가 1순위). 이름이 같은 Danbooru 태그의 뜻과 다를 수 있다.
- **같은 뜻이면 `same_as`**: 그 태그가 Danbooru 태그와 표기만 다른 같은 뜻이면 결과의 `same_as` 칸에 Danbooru 태그 이름(밑줄 표기)을 적고 분류 칸(primary·secondary·rating·regions·identity)은 비운다(분류는 대상에서 온다, `confidence`·`note` 는 채움). 예: big_breasts → `large_breasts`, eyes_closed → `closed_eyes`, absurd_res → `absurdres`, thigh_highs → `thighhighs`. 뜻이 더 넓거나(상위 태그) 좁거나 다르면 직접 분류한다 — 그때도 Danbooru 쪽 분류를 선례로 본다.
- **kind 별로**:
  - `conflict`: Danbooru 폐기 DB 에서 옮긴 분류(Danbooru 위키로 정한 것)다. e621 뜻으로 다시 본다 — 맞으면 같은 값을 다시 적고, 다르면 고친다. Danbooru 가 넓다며 폐기한 상위 태그(female, clothing, genitals)를 e621 은 지금 상위 태그로 쓴다.
  - `suspect`: Danbooru 별칭인데 그 대상이 e621 에도 따로 있거나 meta 라서 자동으로 대상의 분류를 쓰지 않았다(지금 보류). 뜻이 다를 가능성이 크다(`male` 은 Danbooru 에서 `male focus` 의 별칭이지만 e621 에서는 인물의 성별). e621 뜻으로 분류하고, 같은 뜻이면 same_as.
  - `auto`: Danbooru 별칭이라 지금 그 대상의 분류를 자동으로 쓴다. 같은 뜻이면 `same_as` 에 그 대상을 적어 확인하고, 다르면 직접 분류한다(`horn` → Danbooru `single horn` 은 뜻이 좁음 — 직접 분류).
  - `meta`: e621 메타 태그(지금 자동 `meta/technical`). 초상 크롭(headshot·bust·half-length·three-quarter·full-length_portrait) → `composition/framing`, 연도 → `meta/date`, 화면비·해상도·파일·영상 길이·소리 → `meta/technical`(해상도 등급 태그는 Danbooru absurdres 처럼 `meta/quality`), `(artwork)` 매체(digital_media, 3d, pixel, traditional media) → `style/medium`, 화풍 → `style/art_style`, 색 처리 → `style/color`, 그림 속 글자·언어(english_text) → `text/in_image`, 서명·워터마크 → `text/mark`, 형식(comic, animated) → Danbooru 선례대로.
  - `hint`: e621 별칭에 Danbooru 태그 이름이 있다. e621 이 별칭으로 합친 하위·변형일 수 있으니(white_body 의 별칭 white_dragon 등) 같은 뜻일 때만 same_as.
- **e621 관례**(e621 선례가 Danbooru 와 다른 것):
  - 인물의 성별: male, female, intersex, ambiguous_gender, andromorph, gynomorph, herm, maleherm → `subject/count`(1boy·1girl 처럼 그려질 인물). solo·duo·trio·group → `subject/count`. 짝 태그 male/female·male/male·female/female → `action/interaction`(hetero·yaoi·yuri 선례), 등급은 §2 대로 비우거나 g.
  - 몸 형태: anthro, feral, humanoid, taur, semi-anthro → `subject/species`, identity 1(furry 선례). 종 태그(canine, fox)는 종류로 자동이라 배치에 나오지 않는다.
  - 몸 색·털·비늘·깃털: `<색>_body`·`<색>_fur`·`<색>_scales`·`<색>_feathers`, multicolored·two_tone 계열, countershading, markings, `<색>_nose` → `body/features`, identity 1. 사람 피부색(`<색>_skin`)은 `body/skin`, identity 1.
  - 동물 신체(pawpads, claws, muzzle, snout, tuft, fluffy_tail, inner_ear_fluff, digitigrade) → `body/features`, identity 1(§6).
  - 성기 형태는 e621 이 세분한다(animal_genitalia, canine_genitalia, knot, sheath, equine_penis, cloaca, genital_slit, vulva, balls) → `body/lower`, 등급 e, regions hips. 성행위·체위(penetration, male_penetrating, from_behind_position) → `action/sexual`, e. 크기 계열(big_*, huge_*, hyper_*)은 기본 부위를 따르고(가슴은 `body/torso`, identity 1), 성기 크기는 e.
  - 나이: young, cub → `body/skin`, identity 1(loli·shota 선례).
  - 의상 상위 태그(clothing, topwear, bottomwear, legwear, footwear, handwear, headwear, eyewear, armwear) → 해당 `clothes` 서브(clothing 은 `clothes` 메인, eyewear 는 `clothes/accessory`). 입은 정도(clothed, partially_clothed, nude_female, topless_male)는 `clothes/state`.
  - `<부위>_focus`(butt_focus) → `composition/focus`. `<x>_view`(rear_view, side_view, front_view)는 `composition/angle`.
  - 등급은 e621 게시물 대부분이 성인물이어도 §2 대로 **태그 자체가** 그리게 하는 것으로 매긴다.
- e621 고유 밈·캐릭터 별명·약어처럼 위키로도 뜻을 모르면 §7 대로 보류(etc). 사용량이 큰 태그는 대개 위키가 있으니 먼저 위키를 본다.
