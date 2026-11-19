# TTML/IMSC region / style / tts → CSS 映射报告

- 视口: 1280×720px；cellResolution: 32×15；参考实现: imscJS 2.0 + Chrome 157
- 状态: mappable 可直接映射 / partial 部分映射 / emulated 仅可模拟 / unmappable 无法映射

## 样例：multilang.itt

- 观察到的差异项: 13；状态分布: {"unknown":6,"unmappable":1,"partial":3,"emulated":3}

### 生成的 CSS

```css
.ttml-region, .ttml-region p, .ttml-region span,
.ttml-region ruby, .ttml-region rt { margin: 0; }
.ruby-reserve rt {
  font-size: var(--ruby-reserve, 28.8px);
  line-height: var(--ruby-reserve, 28.8px);
  min-height: var(--ruby-reserve, 28.8px);
}
.ruby-reserve { ruby-position: before; }

#bottom {
  position: absolute;
  box-sizing: content-box;
  left: 80px;
  top: 576px;
  width: 1106.4px;
  height: 98.88000000000001px;
  writing-mode: horizontal-tb;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  padding: 8.16px 6.800000000000001px 8.16px 6.800000000000001px;
  background-color: rgba(0,0,0,0.55);
  overflow: hidden;
  z-index: 1;
}

.base {
  font-family: sans-serif;
  font-size: 57.599999999999994px;
  line-height: 76.80000000000001px;
  color: #FFFFFF;
  background-color: rgba(0,0,0,0.72);
}

.ja {
  font-family: sansSerif;
  font-size: 52.800000000000004px;
}

.ar {
  direction: rtl;
  unicode-bidi: plaintext;
}

.em {
  font-style: italic;
  text-decoration-line: underline;
  text-shadow: 2px 2px 4px black;
}

.reserve {
  font-size: 48px;
  line-height: 1;
  /* rubyReserve emulation: .ruby-reserve rt height */;
  --ruby-reserve: 28.799999999999997px;
}
```

### 本文档触发的差异项

| 属性 | 值 | 状态 | 位置 | 原因 |
| --- | --- | --- | --- | --- |
| `xml:id` | bottom | 未收录（xml:id 等非样式属性） | region:bottom | 映射表未收录的属性 |
| `tts:showBackground` | always | 无法映射 | region:bottom | whenActive/always 是 ISD 时间条件语义，CSS 无法按激活态自动切换 |
| `tts:writingMode` | lrtb | 部分映射 | region:bottom | lrtb/rtlbt/tblr/tbrl 需转 horizontal-tb/vertical-rl/vertical-lr |
| `xml:id` | base | 未收录（xml:id 等非样式属性） | style:base | 映射表未收录的属性 |
| `xml:id` | ja | 未收录（xml:id 等非样式属性） | style:ja | 映射表未收录的属性 |
| `xml:lang` | ja | 仅可模拟 | style:ja | 非样式属性，需转 HTML lang，断词/注音依赖浏览器语言引擎 |
| `xml:id` | ar | 未收录（xml:id 等非样式属性） | style:ar | 映射表未收录的属性 |
| `xml:lang` | ar | 仅可模拟 | style:ar | 非样式属性，需转 HTML lang，断词/注音依赖浏览器语言引擎 |
| `tts:direction` | rtl | 部分映射 | style:ar | 需配合 unicode-bidi 隔离，嵌套层级语义不完全一致 |
| `tts:unicodeBidi` | plaintext | 部分映射 | style:ar | 值名需转换（plaintext/embed/bdo/isolated...），隔离覆盖范围有差异 |
| `xml:id` | em | 未收录（xml:id 等非样式属性） | style:em | 映射表未收录的属性 |
| `xml:id` | reserve | 未收录（xml:id 等非样式属性） | style:reserve | 映射表未收录的属性 |
| `tts:rubyReserve` | before 0.6c | 仅可模拟 | style:reserve | 注音预留行高只能用 padding/固定 line-height 模拟，无法按注音宽度自适应；imscJS 自身用空 ruby 占位 |

## 样例：vertical-ruby.itt

- 观察到的差异项: 19；状态分布: {"unknown":9,"partial":7,"unmappable":2,"emulated":1}

### 生成的 CSS

```css
.ttml-region, .ttml-region p, .ttml-region span,
.ttml-region ruby, .ttml-region rt { margin: 0; }
.ruby-reserve rt {
  font-size: var(--ruby-reserve, 28.8px);
  line-height: var(--ruby-reserve, 28.8px);
  min-height: var(--ruby-reserve, 28.8px);
}
.ruby-reserve { ruby-position: before; }

#vright {
  position: absolute;
  box-sizing: content-box;
  left: 1040px;
  top: 48px;
  width: 176px;
  height: 604px;
  writing-mode: vertical-rl;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  padding: 10px 12px 10px 12px;
  background-color: rgba(255,255,255,0.85);
  overflow: hidden;
  z-index: 1;
}

#vlr {
  position: absolute;
  box-sizing: content-box;
  left: 40px;
  top: 48px;
  width: 176px;
  height: 604px;
  writing-mode: vertical-lr;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 10px 12px 10px 12px;
  background-color: rgba(255,255,240,0.9);
  overflow: hidden;
  z-index: 1;
}

.vbase {
  font-family: sansSerif;
  font-size: 67.19999999999999px;
  line-height: 96px;
  color: #111111;
}

.rtext {
  font-size: 33.599999999999994px;
  ruby-position: before;
}

.rbase {

}

.rcontainer {

}

.ralign {
  ruby-align: center;
}

.shear {
  transform: skewX(-10deg);
}

.multi {

}
```

### 本文档触发的差异项

| 属性 | 值 | 状态 | 位置 | 原因 |
| --- | --- | --- | --- | --- |
| `xml:id` | vright | 未收录（xml:id 等非样式属性） | region:vright | 映射表未收录的属性 |
| `tts:writingMode` | tbrl | 部分映射 | region:vright | lrtb/rtlbt/tblr/tbrl 需转 horizontal-tb/vertical-rl/vertical-lr |
| `tts:showBackground` | whenActive | 无法映射 | region:vright | whenActive/always 是 ISD 时间条件语义，CSS 无法按激活态自动切换 |
| `xml:id` | vlr | 未收录（xml:id 等非样式属性） | region:vlr | 映射表未收录的属性 |
| `tts:writingMode` | tblr | 部分映射 | region:vlr | lrtb/rtlbt/tblr/tbrl 需转 horizontal-tb/vertical-rl/vertical-lr |
| `xml:id` | vbase | 未收录（xml:id 等非样式属性） | style:vbase | 映射表未收录的属性 |
| `xml:id` | rtext | 未收录（xml:id 等非样式属性） | style:rtext | 映射表未收录的属性 |
| `tts:ruby` | text | 部分映射 | style:rtext | container 合并语义需 JS 预处理 |
| `tts:rubyPosition` | before | 部分映射 | style:rtext | outside/inter-character 浏览器支持不一致 |
| `xml:id` | rbase | 未收录（xml:id 等非样式属性） | style:rbase | 映射表未收录的属性 |
| `tts:ruby` | base | 部分映射 | style:rbase | container 合并语义需 JS 预处理 |
| `xml:id` | rcontainer | 未收录（xml:id 等非样式属性） | style:rcontainer | 映射表未收录的属性 |
| `tts:ruby` | container | 部分映射 | style:rcontainer | container 合并语义需 JS 预处理 |
| `xml:id` | ralign | 未收录（xml:id 等非样式属性） | style:ralign | 映射表未收录的属性 |
| `tts:rubyAlign` | spaceAround | 部分映射 | style:ralign | Chromium 支持，旧引擎不支持 |
| `xml:id` | shear | 未收录（xml:id 等非样式属性） | style:shear | 映射表未收录的属性 |
| `tts:fontShear` | 10% | 仅可模拟 | style:shear | skewX 影响布局盒，与 TTML 字体内部剪切不等价 |
| `xml:id` | multi | 未收录（xml:id 等非样式属性） | style:multi | 映射表未收录的属性 |
| `tts:multiRowAlign` | start end | 无法映射 | style:multi | TTML2 主/副文本行双值对齐，CSS 无对应属性 |

## CSS 无法直接表达的属性完整清单

### 完全无法映射（unmappable）

| 属性 | CSS 替代 | 原因 |
| --- | --- | --- |
| `tts:showBackground` | 无 | whenActive/always 是 ISD 时间条件语义，CSS 无法按激活态自动切换 |
| `tts:multiRowAlign` | 无 | TTML2 主/副文本行双值对齐，CSS 无对应属性 |
| `tts:linePadding` | 无 | TTML2 行盒两端内边距，CSS 无对应概念 |

### 只能部分映射或模拟（partial / emulated）

| 属性 | 状态 | CSS 替代 | 差异 |
| --- | --- | --- | --- |
| `tts:rubyReserve` | 仅可模拟 | padding/line-height | 注音预留行高只能用 padding/固定 line-height 模拟，无法按注音宽度自适应；imscJS 自身用空 ruby 占位 |
| `tts:fontShear` | 仅可模拟 | transform: skewX() | skewX 影响布局盒，与 TTML 字体内部剪切不等价 |
| `tts:textOutline` | 仅可模拟 | -webkit-text-stroke | 标准 CSS 无 text-outline；描边居中、无 blur、略改字形宽度 |
| `tts:glyphOrientation` | 仅可模拟 | text-orientation/transform | 0/90/180/270 度需逐 span 旋转，影响布局盒 |
| `xml:lang` | 仅可模拟 | lang/:lang() | 非样式属性，需转 HTML lang，断词/注音依赖浏览器语言引擎 |
| `xml:space` | 仅可模拟 | white-space | default/preserve 转 normal/pre，序列化阶段处理空白 |
| `tts:writingMode` | 部分映射 | writing-mode | lrtb/rtlbt/tblr/tbrl 需转 horizontal-tb/vertical-rl/vertical-lr |
| `tts:direction` | 部分映射 | direction | 需配合 unicode-bidi 隔离，嵌套层级语义不完全一致 |
| `tts:unicodeBidi` | 部分映射 | unicode-bidi | 值名需转换（plaintext/embed/bdo/isolated...），隔离覆盖范围有差异 |
| `tts:wrapOption` | 部分映射 | white-space | noWrap -> nowrap；wrapOption 之外的空白折叠语义不覆盖 |
| `tts:textCombine` | 部分映射 | text-combine-upright | TTML2 all/digits 与 CSS 关键字非一一对应 |
| `tts:ruby` | 部分映射 | display:ruby 系列 | container 合并语义需 JS 预处理 |
| `tts:rubyPosition` | 部分映射 | ruby-position | outside/inter-character 浏览器支持不一致 |
| `tts:rubyAlign` | 部分映射 | ruby-align | Chromium 支持，旧引擎不支持 |
| `tts:whitespace` | 部分映射 | white-space | default/preserve/trim 与 CSS 关键字非一一对应 |
| `tts:backgroundClip` | 部分映射 | background-clip | text 值行为不同 |
| `tts:backgroundExtent` | 部分映射 | background-size | 部分定位语法仅部分可映射 |
| `tts:textUnderline` | 部分映射 | text-decoration-line: underline | 线型/粗细/位置支持有限 |
| `tts:textLineThrough` | 部分映射 | text-decoration-line: line-through | 仅基础删除线可映射 |