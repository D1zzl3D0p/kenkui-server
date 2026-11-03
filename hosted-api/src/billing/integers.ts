// Python int accepts decimal digits (including Unicode), signs and digit separators.
export function integerText(text:string):bigint|null {
  let digits=text.replace(/^\p{White_Space}+|\p{White_Space}+$/gu,'');
  if(!/^[+-]?\p{Nd}(?:_?\p{Nd})*$/u.test(digits))return null;
  digits=digits.replaceAll('_','').replace(/\p{Nd}/gu,d=>{
    if(d>='0'&&d<='9')return d;
    const point=d.codePointAt(0)!;
    let first=point;
    while(/\p{Nd}/u.test(String.fromCodePoint(first-1)))first--;
    return String((point-first)%10);
  });
  if(digits.replace(/^[+-]/,'').length>4300)return null;
  return BigInt(digits);
}

// Node 24 exposes the original scalar token, preserving Python's distinction between 1, 1.0 and 1e0.
export function parseExactJson(text:string):unknown {
  return JSON.parse(text,(_key:string,value:unknown,context?:{source?:string})=>
    typeof value==='number' && context?.source && /^-?\d+$/.test(context.source)?BigInt(context.source):value);
}
