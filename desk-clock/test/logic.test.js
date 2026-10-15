const fs=require('fs');
const html=fs.readFileSync(require('path').join(__dirname,'..','index.html'),'utf8');
const m=html.match(/<script>\n([\s\S]*?)\n<\/script>/);
if(!m){console.error('no <script> block found in index.html');process.exit(1);}
const src=m[1];

// Pull a function declaration out of the shipped source by balanced braces.
function grab(name){
  const i=src.indexOf('function '+name+'(');
  if(i<0) throw new Error('missing fn '+name);
  let d=0,j=src.indexOf('{',i);
  for(let k=j;k<src.length;k++){
    if(src[k]==='{')d++; else if(src[k]==='}'){d--; if(!d) return src.slice(i,k+1);}
  }
  throw new Error('unbalanced '+name);
}
function grabLine(re){
  const m=src.match(re);
  if(!m) throw new Error('missing '+re);
  return m[0];
}

const parts=[
  grabLine(/^const pad2 = .*$/m),
  grabLine(/^const clamp = .*$/m),
  grabLine(/^const lerp = .*$/m),
  grabLine(/^const DOW = .*$/m),
  grabLine(/^const ymd = .*$/m),
  grabLine(/^const parseYmd = .*$/m),
  grabLine(/^const SYNODIC = .*$/m),
  grabLine(/^const COMPASS = .*$/m),
  grabLine(/^const compass = .*$/m),
  grabLine(/^const daysInYear = .*$/m),
  grab('isoWeek'), grab('dayOfYear'), grab('moon'),
  grab('beaufort'), grab('occursOn'), grab('parseIcs'),
].join('\n');

const sandbox={};
new Function('exports', parts + `
Object.assign(exports,{pad2,ymd,parseYmd,isoWeek,dayOfYear,daysInYear,moon,beaufort,compass,occursOn,parseIcs});
`)(sandbox);
const F=sandbox;

let pass=0,fail=0;
const eq=(label,got,want)=>{
  const ok=JSON.stringify(got)===JSON.stringify(want);
  ok?pass++:fail++;
  console.log((ok?'  ok  ':'  FAIL')+'  '+label+'  got='+JSON.stringify(got)+(ok?'':'  want='+JSON.stringify(want)));
};
const near=(label,got,want,tol)=>{
  const ok=Math.abs(got-want)<=tol; ok?pass++:fail++;
  console.log((ok?'  ok  ':'  FAIL')+'  '+label+'  got='+got+(ok?'':'  want~'+want));
};

console.log('--- ISO week (known reference dates) ---');
eq('2026-01-01 (Thu) -> wk 1', F.isoWeek(new Date(2026,0,1)), 1);
eq('2026-10-15 -> wk 42', F.isoWeek(new Date(2026,9,15)), 42);
eq('2024-12-30 (Mon) -> wk 1 of 2025', F.isoWeek(new Date(2024,11,30)), 1);
eq('2021-01-01 (Fri) -> wk 53 of 2020', F.isoWeek(new Date(2021,0,1)), 53);

console.log('--- day of year / leap ---');
eq('2026-01-01 -> 1', F.dayOfYear(new Date(2026,0,1)), 1);
eq('2026-12-31 -> 365', F.dayOfYear(new Date(2026,11,31)), 365);
eq('2024-12-31 -> 366', F.dayOfYear(new Date(2024,11,31)), 366);
eq('2026-10-15 -> 288', F.dayOfYear(new Date(2026,9,15)), 288);
eq('daysInYear 2024', F.daysInYear(2024), 366);
eq('daysInYear 2100', F.daysInYear(2100), 365);
eq('daysInYear 2000', F.daysInYear(2000), 366);

console.log('--- moon phase vs published full/new moons ---');
// Reference: 2026 full moons (NASA/USNO): Oct 26 2026 full, Oct 10 2026 new.
near('2026-10-26 illum ~100%', F.moon(new Date(Date.UTC(2026,9,26,4,0))).illum*100, 100, 4);
near('2026-10-10 illum ~0%',   F.moon(new Date(Date.UTC(2026,9,10,3,50))).illum*100, 0, 4);
near('2000-01-21 full illum',  F.moon(new Date(Date.UTC(2000,0,21,4,40))).illum*100, 100, 3);
console.log('    name @ new  =', F.moon(new Date(Date.UTC(2026,9,10,3,50))).name);
console.log('    name @ full =', F.moon(new Date(Date.UTC(2026,9,26,4,0))).name);
console.log('    name @ +7d  =', F.moon(new Date(Date.UTC(2026,9,17,12,0))).name);

console.log('--- compass / beaufort ---');
eq('0deg->N', F.compass(0), 'N');
eq('90deg->E', F.compass(90), 'E');
eq('247deg->WSW', F.compass(247), 'WSW');
eq('359deg->N', F.compass(359), 'N');
eq('calm 0kph->F0', F.beaufort(0), 0);
eq('25kph->F4', F.beaufort(25), 4);
eq('120kph->F12', F.beaufort(120), 12);

console.log('--- event recurrence ---');
const anchor={id:'x',sum:'t',date:'2026-10-15',time:null};
const on=(rep,y,m,d)=>F.occursOn({...anchor,rep},new Date(y,m-1,d));
eq('none: same day', on('none',2026,10,15), true);
eq('none: next day', on('none',2026,10,16), false);
eq('daily: later', on('daily',2026,12,25), true);
eq('daily: before anchor', on('daily',2026,10,14), false);
eq('weekly: +7d', on('weekly',2026,10,22), true);
eq('weekly: +6d', on('weekly',2026,10,21), false);
eq('monthly: next month 15th', on('monthly',2026,11,15), true);
eq('monthly: next month 16th', on('monthly',2026,11,16), false);
eq('yearly: +1y', on('yearly',2027,10,15), true);
eq('yearly: wrong month', on('yearly',2027,11,15), false);

console.log('--- ICS parsing ---');
const ics=[
'BEGIN:VCALENDAR','VERSION:2.0',
'BEGIN:VEVENT','SUMMARY:Dentist\\, upper left','DTSTART;TZID=Europe/Lisbon:20261103T143000','END:VEVENT',
'BEGIN:VEVENT','SUMMARY:All day thing','DTSTART;VALUE=DATE:20261225','END:VEVENT',
'BEGIN:VEVENT','SUMMARY:Ana birthday','DTSTART;VALUE=DATE:19900412','RRULE:FREQ=YEARLY','END:VEVENT',
'BEGIN:VEVENT','SUMMARY:A very long summary that the','  spec folds across lines',
  'DTSTART;VALUE=DATE:20261201','END:VEVENT',
'BEGIN:VEVENT','SUMMARY:Standup','DTSTART:20261104T090000Z','RRULE:FREQ=WEEKLY;BYDAY=WE','END:VEVENT',
'END:VCALENDAR'].join('\r\n');
const evs=F.parseIcs(ics);
eq('event count', evs.length, 5);
eq('escaped comma unescaped', evs[0].sum, 'Dentist, upper left');
eq('local datetime kept', [evs[0].date,evs[0].time], ['2026-11-03','14:30']);
eq('VALUE=DATE -> all day', [evs[1].date,evs[1].time], ['2026-12-25',null]);
eq('RRULE YEARLY mapped', evs[2].rep, 'yearly');
eq('folded line rejoined', evs[3].sum, 'A very long summary that the spec folds across lines');
eq('RRULE WEEKLY mapped', evs[4].rep, 'weekly');
eq('all carry src=ics', evs.every(e=>e.src==='ics'), true);
eq('yearly birthday hits 2026', F.occursOn(evs[2], new Date(2026,3,12)), true);

console.log('\n'+pass+' passed, '+fail+' failed');
process.exit(fail?1:0);
