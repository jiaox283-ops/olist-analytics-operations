"""Run: python src/03_customer_analysis.py --input <宽表CSV路径> --output ."""
from pathlib import Path
import argparse,json
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from analysis_contract import low_review, valid_delay, valid_duration, valid_review

def main():
 p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',default='.');a=p.parse_args()
 root=Path(a.output);out=root/'results'/'module3';fig=root/'figures'
 out.mkdir(parents=True,exist_ok=True);fig.mkdir(parents=True,exist_ok=True)
 d=pd.read_csv(a.input,dtype={'order_id':str,'customer_unique_id':str})
 assert d.order_id.notna().all() and d.order_id.is_unique,'order_id must be unique'
 d['purchase']=pd.to_datetime(d.order_purchase_timestamp,errors='coerce')
 end=pd.Timestamp('2018-09-01');start=pd.Timestamp('2017-01-01')
 eligible=(d.order_status.eq('delivered') & d.purchase.lt(end) & d.purchase.notna() & d.customer_unique_id.notna() & d.customer_unique_id.str.strip().ne('') & d.item_amount.notna() & d.item_amount.ge(0))
 x=d.loc[eligible].copy();x['month']=x.purchase.dt.to_period('M')
 x['valid_review']=valid_review(x)
 x['delay']=valid_delay(x)
 x['low_review']=low_review(x.valid_review)
 x['duration']=valid_duration(x)
 u=x.groupby('customer_unique_id').agg(first_purchase=('purchase','min'),last_purchase=('purchase','max'),F=('order_id','nunique'),M=('item_amount','sum'))
 u['R']=(end-u.last_purchase.dt.normalize()).dt.days
 # Keep tied monetary values together; F is deliberately not forced into quantiles.
 q1,q2=u.M.quantile([1/3,2/3]);u['R_score']=np.select([u.R.le(90),u.R.le(180)],[3,2],default=1)
 u['F_score']=np.select([u.F.eq(1),u.F.eq(2)],[1,2],default=3)
 u['M_score']=np.select([u.M.le(q1),u.M.le(q2)],[1,2],default=3)
 u['segment']=np.select([u.F.ge(2)&u.R.le(90),u.F.ge(2),u.F.eq(1)&u.R.le(90)&u.M_score.eq(3),u.F.eq(1)&u.R.le(90),u.F.eq(1)&u.R.le(180)],['近期复购客户','较久未购复购客户','近期高金额单购客户','近期普通单购客户','中期未购单购客户'],default='较久未购单购客户')
 u['repeat']=u.F.ge(2).astype(int)
 x=x.merge(u[['segment']],left_on='customer_unique_id',right_index=True,validate='many_to_one')
 s=u.groupby('segment').agg(customers=('F','size'),orders=('F','sum'),item_amount=('M','sum'),R_median=('R','median'),F_mean=('F','mean'),M_median=('M','median'),repeat_rate=('repeat','mean'))
 s['customer_share']=s.customers/len(u);s['amount_share']=s.item_amount/u.M.sum();s['item_aov']=s.item_amount/s.orders
 e=x.groupby('segment').agg(review_valid_orders=('valid_review','count'),review_mean=('valid_review','mean'),low_review_rate=('low_review','mean'),delay_valid_orders=('delay','count'),delay_rate=('delay','mean'),delivery_valid_orders=('duration','count'),delivery_days_mean=('duration','mean'))
 s=s.join(e).sort_values('customers',ascending=False)
 repeat=u.groupby('F').agg(customers=('M','size'),item_amount=('M','sum'));repeat['customer_share']=repeat.customers/len(u)
 x['cohort']=x.groupby('customer_unique_id').month.transform('min')
 x['age']=x.month.astype('int64')-x.cohort.astype('int64')
 counts=x.groupby(['cohort','age']).customer_unique_id.nunique().unstack()
 sizes=x.groupby('cohort').customer_unique_id.nunique()
 counts=counts.reindex(index=pd.period_range('2017-01','2018-08',freq='M'),columns=range(20))
 for c in counts.index:
  maxage=pd.Period('2018-08','M').ordinal-c.ordinal
  counts.loc[c,0:maxage]=counts.loc[c,0:maxage].fillna(0)
 rates=counts.div(sizes.reindex(counts.index),axis=0)
 def experience(g):
  n=g.valid_review.count();k=int(g.low_review.sum())
  z=1.96;ph=k/n if n else np.nan;den=1+z*z/n if n else np.nan
  center=(ph+z*z/(2*n))/den if n else np.nan
  half=z*np.sqrt(ph*(1-ph)/n+z*z/(4*n*n))/den if n else np.nan
  return pd.Series({'orders':len(g),'review_valid_orders':n,'review_mean':g.valid_review.mean(),'low_review_orders':k,'low_review_rate':ph,'low_review_ci95_low':center-half,'low_review_ci95_high':center+half})
 exp=pd.DataFrame({('delayed' if v==1 else 'on_time'):experience(g) for v,g in x[x.delay.notna()].groupby('delay')}).T
 # Equal 90-day opportunity from first calendar purchase date; retrospective delivered-order sample.
 first=u.first_purchase.dt.normalize();mature=u.index[first.le(end-pd.Timedelta(days=90))]
 t=x.merge(u[['first_purchase']],left_on='customer_unique_id',right_index=True)
 t['first_day']=t.first_purchase.dt.normalize();t['day']=t.purchase.dt.normalize()
 repids=set(t.loc[t.customer_unique_id.isin(mature)&t.day.gt(t.first_day)&t.day.lt(t.first_day+pd.Timedelta(days=90)),'customer_unique_id'])
 checks={'input_orders':len(d),'input_duplicate_orders':int(d.order_id.duplicated().sum()),'analysis_orders':len(x),'analysis_customers':len(u),'analysis_item_amount':float(x.item_amount.sum()),'customer_amount_difference':float(u.M.sum()-x.item_amount.sum()),'segment_orders_difference':int(s.orders.sum()-len(x)),'repeat_customers':int(u.repeat.sum()),'repeat_customer_rate':float(u.repeat.mean()),'M_cut_1':float(q1),'M_cut_2':float(q2),'90d_eligible_customers':len(mature),'90d_next_day_repeat_customers':len(repids),'90d_next_day_repeat_rate':len(repids)/len(mature),'delivered_after_cutoff':int((d.order_status.eq('delivered')&d.purchase.ge(end)).sum()),'excluded_delivered_before_cutoff':int((d.order_status.eq('delivered')&d.purchase.lt(end)&~eligible).sum())}
 assert abs(checks['customer_amount_difference'])<0.01 and checks['segment_orders_difference']==0
 for name,data in [('01_customer_rfm',u),('02_segment_profiles',s),('03_purchase_frequency',repeat),('04_cohort_counts',counts),('05_cohort_rates',rates),('06_cohort_sizes',sizes.rename('customers').to_frame()),('07_delivery_experience',exp)]:data.to_csv(out/(name+'.csv'),encoding='utf-8-sig')
 (out/'08_validation.json').write_text(json.dumps(checks,indent=2,ensure_ascii=False),encoding='utf-8')
 plt.rcParams.update({'font.size':10,'figure.dpi':160})
 names={'近期复购客户':'Recent repeat','较久未购复购客户':'Older repeat','近期高金额单购客户':'Recent high-spend single','近期普通单购客户':'Recent regular single','中期未购单购客户':'91-180d single','较久未购单购客户':'>180d single'}
 f,ax=plt.subplots(figsize=(10,4));ax.barh([names[v] for v in s.index],s.customer_share*100,color='#3979a8');ax.invert_yaxis();ax.set_xlabel('Share of customers (%)');ax.set_title('Historical customer segments');f.tight_layout();f.savefig(fig/'m3_01_rfm_segments.png');plt.close(f)
 f,ax=plt.subplots(figsize=(12,7));ar=rates.to_numpy()*100;im=ax.imshow(np.ma.masked_invalid(ar),vmin=0,vmax=1,cmap='Blues',aspect='auto');ax.set_yticks(range(len(rates)),[str(v) for v in rates.index]);ax.set_xticks(range(20));ax.set_xlabel('Calendar months since first observed purchase');ax.set_title('Monthly repeat purchase (%) | M0 = 100%, color capped at 1%')
 for i in range(ar.shape[0]):
  for j in range(ar.shape[1]):
   if not np.isnan(ar[i,j]):ax.text(j,i,f'{ar[i,j]:.1f}',ha='center',va='center',fontsize=6,color='white' if ar[i,j]>.55 else '#263445')
 f.colorbar(im,ax=ax,label='Repeat purchase (%)');f.tight_layout();f.savefig(fig/'m3_02_cohort.png');plt.close(f)
 f,ax=plt.subplots(figsize=(7,4));ax.bar(exp.index,exp.low_review_rate*100,color=['#d88749','#3979a8']);ax.set_ylabel('Low review rate (1-2 stars, %)');ax.set_title('Delivery status and customer reviews');f.tight_layout();f.savefig(fig/'m3_03_experience.png');plt.close(f)
 print(json.dumps(checks,ensure_ascii=False));print(s.to_string());print(exp.to_string())
if __name__=='__main__':main()
