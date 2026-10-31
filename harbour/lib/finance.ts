/** Canadian fixed mortgage convention: nominal annual rate, semiannual compounding. */
export function ownership(price:number,downPercent:number,annualRate:number,years:number,tax:number,strata:number,insurance:number,maintenance:number){
 const down=price*downPercent/100, principal=price-down;
 const minimumDown=price>=1500000?price*.2:Math.min(price,500000)*.05+Math.max(0,price-500000)*.1;
 const ltv=principal/price;
 const eligible=down>=minimumDown&&price>0&&price<1500000;
 const premiumRate=eligible&&ltv>.8?(ltv<=.85?.028:ltv<=.9?.031:.04)+(years>25?.002:0):0;
 const premium=principal*premiumRate;
 const r=Math.pow(1+annualRate/200,1/6)-1,n=years*12;
 const mortgage=r===0?(principal+premium)/n:(principal+premium)*r/(1-Math.pow(1+r,-n));
 const transfer=Math.min(price,200000)*.01+Math.max(0,Math.min(price-200000,1800000))*.02+Math.max(0,price-2000000)*.03+Math.max(0,price-3000000)*.02;
 return {mortgage,premium,minimumDown,valid:price>0&&down>=minimumDown&&down<=price,total:mortgage+tax/12+strata+insurance+maintenance,down,transfer,cash:down+transfer};
}
