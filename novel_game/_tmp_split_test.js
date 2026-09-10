function splitSentences(text) {
  const out = [];
  const re = /([^。！？!?…\n]*[。！？!?…]+[」”’』）)]*)|([^。！？!?…\n]+)/g;
  let m;
  while ((m = re.exec(text))) {
    const seg = m[0].replace(/\n+/g, '').trim();
    if (seg) out.push(seg);
  }
  return out;
}
const t1 = '美猴王纵身一跃，腾空而起。只见那南天门云雾缭绕，天兵天将分列两侧！「来者何人？」他高声喝道……\n\n玉帝端坐灵霄宝殿。';
console.log('T1:', JSON.stringify(splitSentences(t1), null, 1));
const t2 = '短句。第二句！第三句？';
console.log('T2:', JSON.stringify(splitSentences(t2)));
const t3 = '无标点残尾';
console.log('T3:', JSON.stringify(splitSentences(t3)));
