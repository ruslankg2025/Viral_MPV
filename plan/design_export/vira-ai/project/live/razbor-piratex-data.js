// VIRA · Razbor Piratex prototype — mock data

window.RZP_VIDEO_META = {
  platform: 'INSTAGRAM',
  author: 'positivityasparents',
  duration_sec: 56,
  views: 8700,
  likes: 1600,
  comments: 10,
  er: 18.6,
  multiplier: 0.5,
  thumbnail_hue: 18,
  url: 'https://www.instagram.com/p/DXsEyErDrJA',
};

// 20 frames covering 0:56
window.RZP_FRAMES = [
  { t:'0:00', type:'animation',   text_orig:'world…',         text_ru:'мир…',           visual:'Анимация — ребёнок бежит к взрослому в дверях, тёплый закатный свет, размытые силуэты на заднем плане.', hue: 22 },
  { t:'0:01', type:'animation',   text_orig:'BEST FEELING',   text_ru:'ЛУЧШЕЕ ЧУВСТВО', visual:'Текст крупно по центру кадра, чёрный фон, тонкая anti-aliased засечка, лёгкий glow.', hue: 28 },
  { t:'0:02', type:'talking_head',text_orig:'',               text_ru:'',               visual:'Молодая женщина в кадре, взгляд прямо в камеру, светлая кухня в боке, естественный свет.', hue: 38 },
  { t:'0:05', type:'cutaway',     text_orig:'face lights up', text_ru:'лицо загорается',visual:'Крупный план — лицо ребёнка, который улыбается, глаза светятся, расфокус на фоне.',  hue: 14 },
  { t:'0:08', type:'cutaway',     text_orig:'feet run faster',text_ru:'ноги несут быстрее',visual:'Низкий ракурс, маленькие босые ноги бегут по деревянному полу, motion blur.', hue: 32 },
  { t:'0:11', type:'talking_head',text_orig:'',               text_ru:'',               visual:'Та же героиня, чуть смещена вправо, говорит со спокойной улыбкой, мягкий backlight.', hue: 38 },
  { t:'0:14', type:'text_overlay',text_orig:'YOUR NAME',      text_ru:'ТВОЁ ИМЯ',       visual:'Полноэкранный текст-плашка, минималистичный sans-serif, центровка, тёмный фон.', hue: 220 },
  { t:'0:17', type:'cutaway',     text_orig:'shouting',       text_ru:'кричит',         visual:'Ребёнок зовёт родителя, открытый рот, поднятые руки, размытие фона.', hue: 16 },
  { t:'0:20', type:'split_screen',text_orig:'',               text_ru:'',               visual:'Сверху — лицо родителя, снизу — лицо ребёнка, синхронные улыбки, разделитель белой линией.', hue: 200 },
  { t:'0:23', type:'talking_head',text_orig:'',               text_ru:'',               visual:'Героиня держит чашку, мягкий жест рукой к камере, говорит вкрадчиво.', hue: 38 },
  { t:'0:27', type:'b_roll',      text_orig:'',               text_ru:'',               visual:'B-roll — солнечный свет через тюль, медленный pan вправо, тёплая палитра.', hue: 30 },
  { t:'0:30', type:'cutaway',     text_orig:'bills',          text_ru:'счета',           visual:'Стопка конвертов на столе, расфокус, мягкий боковой свет.', hue: 200 },
  { t:'0:33', type:'animation',   text_orig:'NO TITLE',       text_ru:'НИКАКАЯ ДОЛЖНОСТЬ',visual:'Текст разбивается на буквы, частицы, лёгкий kinetic-typo эффект.', hue: 280 },
  { t:'0:36', type:'cutaway',     text_orig:'amount of $$$',  text_ru:'размер денег',   visual:'Стопка купюр, медленный наезд, неоновый отсвет от соседнего источника.', hue: 50 },
  { t:'0:40', type:'talking_head',text_orig:'',               text_ru:'',               visual:'Героиня смотрит вниз, потом поднимает взгляд, eye-contact с камерой.', hue: 38 },
  { t:'0:43', type:'b_roll',      text_orig:'',               text_ru:'',               visual:'Дверная ручка крупно, рука тянется к ней, тёплый свет из щели.', hue: 24 },
  { t:'0:46', type:'cutaway',     text_orig:'smile bigger',   text_ru:'улыбайся шире',  visual:'Родитель улыбается, медленный наезд камеры, лёгкое размытие.', hue: 38 },
  { t:'0:49', type:'reaction',    text_orig:'❤',              text_ru:'❤',              visual:'Сердечко-эмодзи увеличивается на весь кадр, мягкая пульсация, розовое свечение.', hue: 340 },
  { t:'0:52', type:'talking_head',text_orig:'',               text_ru:'',               visual:'Героиня прощается с камерой, улыбается, лёгкий kinetic-фон позади.', hue: 38 },
  { t:'0:54', type:'text_overlay',text_orig:'COME HOME',      text_ru:'ВЕРНИСЬ ДОМОЙ',  visual:'Финальный slate, белый текст на чёрном, тонкая виньетка по краям.', hue: 0 },
];

window.RZP_TRANSCRIPT = [
  ['0:00','Seeing your kids excited to see you…'],
  ['0:03','It\'s the way their face lights up…'],
  ['0:07','The way their feet run faster…'],
  ['0:11','The way they shout your name like…'],
  ['0:15','Like nothing else in the world matters.'],
  ['0:19','Not the bills, not the laundry,'],
  ['0:22','Not the tough day at work.'],
  ['0:26','Just you. Walking through that door.'],
  ['0:30','And you realise — this is it.'],
  ['0:34','This is the moment you\'ll miss.'],
  ['0:38','And no title or amount of success'],
  ['0:42','Will ever feel as warm as this welcome.'],
  ['0:46','So next time you come home tired…'],
  ['0:50','Open the door slower. Smile bigger.'],
  ['0:54','They are watching. They are remembering.'],
];

// ─── Структурированная стратегия (по разделам) ───────────────────
window.RZP_STRATEGY_SECTIONS = [
  {
    id: 'why',
    title: 'Почему этот ролик залетел',
    body: 'Зацепка работает в первые 1.2 секунды — не на лозунге, а на сенсорной детали («ноги несут быстрее»). Это активирует зеркальные нейроны: зритель видит свою сцену, не чужую. Дальше идёт классическая рамка «контраст–возврат»: карьера и быт обесцениваются на фоне одной открытой двери. Ролик не учит, не критикует, не продаёт — он валидирует. Это редкость в нише, поэтому ER выше среднего ×2.4.'
  },
  {
    id: 'audience',
    title: 'Целевая аудитория',
    body: 'Родители 28–42, фокус на «работающие мамы», «уставшие папы». Это аудитория, которая ищет не лайфхаки, а валидацию своих повседневных решений. Они уже устали слушать «организуй своё время лучше» и реагируют на голос, который говорит «то, что ты уже делаешь — правильное». Вторичная аудитория — будущие родители 24–32, которые сохраняют такие ролики «на потом».'
  },
  {
    id: 'triggers',
    title: 'Эмоциональные триггеры',
    body: 'Контраст «карьера vs возвращение домой» — вечный. Здесь он подан без морализаторства, через детали → ниже сопротивление. Ключевые триггеры в последовательности: ностальгия (1–6 сек), узнавание себя (7–22 сек), страх упустить момент (23–42 сек), катарсис (43–56 сек). Никаких призывов к действию — только обещание, что зритель «не один такой».'
  },
  {
    id: 'windows',
    title: 'Окна публикации',
    body: 'Будни 06:30–08:00 — по дороге на работу, родители scrollят перед сменой режима. Будни 19:00–21:00 — после укладывания детей, момент тишины и рефлексии. Выходные — только если ролик про «момент, который чуть не пропустили». Худшее окно — рабочее (10:00–17:00 будни): аудитория эмоционально закрыта.'
  },
  {
    id: 'recipe',
    title: 'Что делать тебе',
    body: 'Не копируй сценарий — копируй структуру: 3 сенсорные детали (визуальные/звуковые) → разворот на абстракцию (вечные ценности) → возврат к одной конкретной картинке (открытая дверь / звук имени). Длина 45–58 сек. Голос — мягкий, без надрыва. Финальный кадр — буквально 1 слово на тёмном фоне. Не используй музыку громче −18 dB: она съест эмоциональную тишину между фразами.'
  },
];

// ─── Прошлые разборы для ленты в AI-студии ──────────────────────
window.RZP_LIBRARY = [
  { id:'cur', author:'positivityasparents', platform:'INSTAGRAM', dur:56, views:8700, likes:1600, comments:10, er:18.6, mult:0.5, hue:18, title:'Когда твой ребёнок несётся к тебе…', when:'только что', current:true },
  { id:'r2',  author:'alexeylinetsky',      platform:'INSTAGRAM', dur:61, views:9300, likes:410,  comments:22, er:4.6,  mult:3.6, hue:20, title:'Просили в рилс — делаю',         when:'5 ч назад' },
  { id:'r3',  author:'alexeylinetsky',      platform:'INSTAGRAM', dur:76, views:11700,likes:682,  comments:59, er:6.3,  mult:4.7, hue:240,title:'Если вы внедрите эту простую…',  when:'1 день назад' },
  { id:'r4',  author:'robdialjr',           platform:'INSTAGRAM', dur:79, views:8200, likes:752,  comments:14, er:9.3,  mult:0.6, hue:30, title:'Your subconscious runs 95%…',     when:'1 день назад' },
  { id:'r5',  author:'mindsetmentor',       platform:'YOUTUBE',   dur:48, views:14300,likes:1100, comments:88, er:8.3,  mult:2.1, hue:340,title:'Stop chasing money. Start…',      when:'2 дня назад' },
  { id:'r6',  author:'thedailystoic',       platform:'TIKTOK',    dur:42, views:62000,likes:4800, comments:312,er:8.2,  mult:5.4, hue:140,title:'You don\'t need more time',       when:'3 дня назад' },
  { id:'r7',  author:'jaymorrisonacademy',  platform:'INSTAGRAM', dur:68, views:5400, likes:380,  comments:18, er:7.4,  mult:0.9, hue:280,title:'The difference between rich and…',when:'4 дня назад' },
  { id:'r8',  author:'davenicolette',       platform:'YOUTUBE',   dur:55, views:22000,likes:1900, comments:140,er:9.3,  mult:3.0, hue:60, title:'I quit my job at 28. Here\'s what…',when:'1 неделя назад' },
];

// ─── В обработке (второй pipeline) ──────────────────────────────
window.RZP_PROCESSING = [
  { id:'p1', source:'positivityasparents', title:'Адаптация: «Когда твой ребёнок несётся…»', step:1, total:5, hue:18, when:'30 сек назад', current:true },
  { id:'p2', source:'thedailystoic',       title:'Адаптация: «You don\'t need more time»',  step:3, total:5, hue:140,when:'12 мин назад' },
];

// ─── Готовые (просто mock) ──────────────────────────────────────
window.RZP_READY = [
  { id:'d1', title:'Сценарий «Открой дверь медленнее»',  source:'positivityasparents', hue:24, when:'вчера' },
  { id:'d2', title:'Сценарий «Что ты помнишь о маме»',   source:'mindsetmentor',       hue:340,when:'2 дня назад' },
  { id:'d3', title:'Сценарий «Не время — внимание»',     source:'thedailystoic',       hue:140,when:'3 дня назад' },
  { id:'d4', title:'Сценарий «12 секунд встречи»',       source:'robdialjr',           hue:30, when:'5 дней назад' },
  { id:'d5', title:'Сценарий «Тёплый звук имени»',       source:'davenicolette',       hue:60, when:'1 неделя назад' },
];
