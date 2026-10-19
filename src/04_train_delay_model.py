"""Reproduce temporal delay-risk experiment. No retrospective outcome features."""
import argparse,json,hashlib,platform
from pathlib import Path
import numpy as np
import pandas as pd
import sklearn,joblib
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import OneHotEncoder,StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (roc_auc_score,average_precision_score,precision_score,recall_score,f1_score,fbeta_score,confusion_matrix,brier_score_loss,precision_recall_curve,roc_curve)
from sklearn.inspection import permutation_importance
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

NUM=['item_amount','freight_amount','item_count','distinct_product_count','seller_count','promise_days','freight_ratio','purchase_month','purchase_weekday','purchase_hour','time_index']
CAT=['customer_state'];FEATURES=NUM+CAT
def features(d):
 t=pd.to_datetime(d.order_purchase_timestamp);e=pd.to_datetime(d.order_estimated_delivery_date)
 f=d[['item_amount','freight_amount','item_count','distinct_product_count','seller_count','customer_state']].copy()
 f['promise_days']=(e.dt.normalize()-t.dt.normalize()).dt.days
 f['freight_ratio']=f.freight_amount/f.item_amount.where(f.item_amount.gt(0))
 f['purchase_month']=t.dt.month;f['purchase_weekday']=t.dt.dayofweek;f['purchase_hour']=t.dt.hour;f['time_index']=(t-pd.Timestamp('2017-01-01')).dt.total_seconds()/86400
 f[NUM]=f[NUM].replace([np.inf,-np.inf],np.nan)
 f['customer_state']=f.customer_state.fillna('UNKNOWN')
 return f[FEATURES]
def prep(scale=False):
 n=[('impute',SimpleImputer(strategy='median',add_indicator=True))]
 if scale:n.append(('scale',StandardScaler()))
 return ColumnTransformer([('num',Pipeline(n),NUM),('cat',OneHotEncoder(handle_unknown='ignore',sparse_output=False),CAT)])
def metrics(y,p,threshold):
 pred=p>=threshold;tn,fp,fn,tp=confusion_matrix(y,pred,labels=[0,1]).ravel();k=max(1,int(np.ceil(len(y)*.1)));idx=np.argsort(-np.asarray(p),kind='stable')[:k]
 return dict(n=len(y),positives=int(np.sum(y)),prevalence=float(np.mean(y)),roc_auc=roc_auc_score(y,p),pr_auc_ap=average_precision_score(y,p),brier=brier_score_loss(y,p),threshold=threshold,precision=precision_score(y,pred,zero_division=0),recall=recall_score(y,pred,zero_division=0),f1=f1_score(y,pred,zero_division=0),top10_precision=float(np.mean(np.asarray(y)[idx])),top10_recall=float(np.sum(np.asarray(y)[idx])/np.sum(y)),top10_lift=float(np.mean(np.asarray(y)[idx])/np.mean(y)),tn=int(tn),fp=int(fp),fn=int(fn),tp=int(tp),alerts=int(pred.sum()))
def main():
 try:
  import xgboost
 except ImportError as exc:
  raise RuntimeError('需要 requirements.txt 中的 xgboost==3.4.1；为保持模块四冻结选型，不使用替代模型。') from exc
 if xgboost.__version__ != '3.4.1':
  raise RuntimeError(f'需要 xgboost==3.4.1，当前为 {xgboost.__version__}')
 a=argparse.ArgumentParser();a.add_argument('--input',required=True);a.add_argument('--output',default='.');a=a.parse_args();root=Path(a.output)
 for n in ['results/module4','figures','models']: (root/n).mkdir(parents=True,exist_ok=True)
 r=root/'results'/'module4';g=root/'figures';d=pd.read_csv(a.input,dtype={'order_id':str,'customer_unique_id':str});assert d.order_id.is_unique
 t=pd.to_datetime(d.order_purchase_timestamp);delivery=pd.to_datetime(d.order_delivered_customer_date);base=d.order_status.eq('delivered')&d.usable_delay_label.eq(1)&d.is_delayed.isin([0,1])&t.ge('2017-01-01')&t.lt('2018-09-01')
 masks={'train':base&t.lt('2018-02-01')&delivery.lt('2018-04-01'),'validation':base&t.ge('2018-02-01')&t.lt('2018-04-01')&delivery.lt('2018-06-01'),'test':base&t.ge('2018-06-01')}
 X={k:features(d.loc[v]) for k,v in masks.items()};Y={k:d.loc[v,'is_delayed'].astype(int).to_numpy() for k,v in masks.items()}
 assert all(len(np.unique(v))==2 for v in Y.values());assert not any(set(d.loc[masks[u],'order_id'])&set(d.loc[masks[v],'order_id']) for u,v in [('train','validation'),('train','test'),('validation','test')])
 splits=[]
 for k,m in masks.items():splits.append(dict(split=k,orders=int(m.sum()),delayed=int(Y[k].sum()),delay_rate=float(Y[k].mean()),purchase_min=str(t[m].min()),purchase_max=str(t[m].max()),delivery_max=str(delivery[m].max())))
 pd.DataFrame(splits).to_csv(r/'01_splits.csv',index=False,encoding='utf-8-sig')
 # Simple rule: historical smoothed risk lookup by promised calendar days.
 # Smooth training empirical late rates by promised days, with 100 pseudo-observations.
 tr=pd.DataFrame({'days':X['train'].promise_days.to_numpy(),'y':Y['train']});agg=tr.groupby('days').y.agg(['sum','count']);prior=Y['train'].mean();lookup=(agg['sum']+100*prior)/(agg['count']+100)
 rule={k:x.promise_days.map(lookup).fillna(prior).to_numpy() for k,x in X.items()}
 agg.assign(smoothed_rate=lookup).to_csv(r/'02_rule_lookup.csv',encoding='utf-8-sig')
 candidates={}
 for c in [.1,1.0]:candidates[f'Logistic_C{c}']=Pipeline([('preprocess',prep(True)),('model',LogisticRegression(C=c,max_iter=1000,random_state=42))])
 for depth in [2,4]:candidates[f'XGBoost_depth{depth}']=Pipeline([('preprocess',prep()),('model',xgboost.XGBClassifier(n_estimators=250,max_depth=depth,learning_rate=.05,subsample=.85,colsample_bytree=.9,min_child_weight=20,reg_lambda=5,objective='binary:logistic',eval_metric='logloss',random_state=42,n_jobs=2))])
 backend='XGBoost';version=xgboost.__version__
 scores=[];probs={'Rule':rule};models={}
 for name,model in candidates.items():
  model.fit(X['train'],Y['train']);v=model.predict_proba(X['validation'])[:,1];scores.append({'candidate':name,'validation_ap':average_precision_score(Y['validation'],v),'validation_roc_auc':roc_auc_score(Y['validation'],v)});models[name]=model;probs[name]={'validation':v}
  print(name,scores[-1],flush=True)
 bestlog=max([n for n in candidates if n.startswith('Logistic')],key=lambda n:next(s['validation_ap'] for s in scores if s['candidate']==n));besttree=max([n for n in candidates if not n.startswith('Logistic')],key=lambda n:next(s['validation_ap'] for s in scores if s['candidate']==n))
 selected=max([bestlog,besttree],key=lambda n:next(s['validation_ap'] for s in scores if s['candidate']==n));chosen=['Rule',bestlog,besttree]
 pd.DataFrame(scores).to_csv(r/'03_validation_candidates.csv',index=False,encoding='utf-8-sig')
 # Freeze model and F2 threshold exclusively on validation, without retraining.
 thresholds={};rows=[]
 for name in chosen:
  pv=probs[name]['validation'];grid=np.unique(np.quantile(pv,np.linspace(0,1,201)));thr=max(grid,key=lambda z:fbeta_score(Y['validation'],pv>=z,beta=2,zero_division=0));thresholds[name]=float(thr)
  if name!='Rule':probs[name]['test']=models[name].predict_proba(X['test'])[:,1];joblib.dump(models[name],root/'models'/(name+'.joblib'))
  for split in ['validation','test']:rows.append({'model':name,'split':split,**metrics(Y[split],probs[name][split],float(thr))})
 pd.DataFrame(rows).to_csv(r/'04_metrics.csv',index=False,encoding='utf-8-sig')
 meta={'selected_model':selected,'tree_backend':backend,'tree_version':version,'sklearn_version':sklearn.__version__,'python_version':platform.python_version(),'input_sha256':hashlib.sha256(Path(a.input).read_bytes()).hexdigest(),'features':FEATURES,'thresholds':thresholds,'train_purged_labels':int((base&t.lt('2018-02-01')&delivery.ge('2018-04-01')).sum()),'validation_purged_labels':int((base&t.ge('2018-02-01')&t.lt('2018-04-01')&delivery.ge('2018-06-01')).sum()),'input_orders':len(d),'eligible_orders':int(base.sum()),'excluded_orders':int((~base).sum()),'train_test_customer_overlap':len(set(d.loc[masks['train'],'customer_unique_id'])&set(d.loc[masks['test'],'customer_unique_id']))}
 (r/'05_run_config.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
 test=d.loc[masks['test'],['order_id','order_purchase_timestamp','customer_state','is_delayed']].reset_index(drop=True);test['risk_score']=probs[selected]['test'];test['predicted_alert']=test.risk_score.ge(thresholds[selected]);test.sort_values(['risk_score','order_id'],ascending=[False,True]).to_csv(r/'06_test_risk_orders.csv',index=False,encoding='utf-8-sig')
 # Overall Top-K and daily capacity comparisons are retrospective ranking metrics.
 top=[];y=Y['test'];base_rate=y.mean()
 for name in chosen:
  pred=probs[name]['test'];order=np.argsort(-pred,kind='stable')
  for frac in [.01,.05,.1,.2]:
   k=int(np.ceil(len(y)*frac));hit=int(y[order[:k]].sum());top.append(dict(model=name,selection='overall',capacity=frac,selected=k,hits=hit,precision=hit/k,recall=hit/y.sum(),lift=(hit/k)/base_rate,random_expected_hits=k*base_rate))
  days=pd.to_datetime(test.order_purchase_timestamp).dt.date
  for kday in [10,20,50]:
   inds=[]
   for day,ix in days.groupby(days).groups.items():inds.extend(sorted(ix,key=lambda j:(-pred[j],test.order_id.iloc[j]))[:kday])
   hit=int(y[inds].sum());randomhits=sum(min(kday,len(ix))*y[list(ix)].mean() for ix in days.groupby(days).groups.values());top.append(dict(model=name,selection='daily',capacity=kday,selected=len(inds),hits=hit,precision=hit/len(inds),recall=hit/y.sum(),lift=(hit/len(inds))/base_rate,random_expected_hits=randomhits))
 pd.DataFrame(top).to_csv(r/'07_topk.csv',index=False,encoding='utf-8-sig')
 imp=permutation_importance(models[selected],X['validation'],Y['validation'],scoring='average_precision',n_repeats=3,random_state=42,n_jobs=1)
 importance=pd.DataFrame({'feature':FEATURES,'validation_ap_drop_mean':imp.importances_mean,'std':imp.importances_std}).sort_values('validation_ap_drop_mean',ascending=False);importance.to_csv(r/'08_feature_importance.csv',index=False,encoding='utf-8-sig')
 month=test.assign(month=pd.to_datetime(test.order_purchase_timestamp).dt.to_period('M').astype(str)).groupby('month').indices;mr=[]
 for m,ix in month.items():mr.append({'month':m,**metrics(y[ix],probs[selected]['test'][ix],thresholds[selected])})
 pd.DataFrame(mr).to_csv(r/'09_monthly_stability.csv',index=False,encoding='utf-8-sig')
 errors=test.assign(predicted=np.where(test.predicted_alert,1,0));errors['error_type']=np.select([errors.is_delayed.eq(1)&errors.predicted.eq(0),errors.is_delayed.eq(0)&errors.predicted.eq(1)],['false_negative','false_positive'],default='correct');errors.groupby(['customer_state','error_type']).size().unstack(fill_value=0).to_csv(r/'10_errors_by_state.csv',encoding='utf-8-sig')
 fig,axs=plt.subplots(1,2,figsize=(11,4))
 for name in chosen:
  pp=probs[name]['test'];fpr,tpr,_=roc_curve(y,pp);pr,re,_=precision_recall_curve(y,pp);axs[0].plot(fpr,tpr,label=name);axs[1].plot(re,pr,label=name)
 axs[0].plot([0,1],[0,1],'--',color='gray');axs[0].set(xlabel='False positive rate',ylabel='True positive rate',title='Test ROC');axs[1].axhline(base_rate,color='gray',linestyle='--');axs[1].set(xlabel='Recall',ylabel='Precision',title='Test precision-recall');[ax.legend(fontsize=8) for ax in axs];fig.tight_layout();fig.savefig(g/'m4_01_roc_pr.png',dpi=160);plt.close(fig)
 fig,ax=plt.subplots(figsize=(7,4));z=importance.head(10).iloc[::-1];ax.barh(z.feature,z.validation_ap_drop_mean,color='#3979a8');ax.set(xlabel='Validation AP drop after permutation',title='Predictive importance (not causal)');fig.tight_layout();fig.savefig(g/'m4_02_feature_importance.png',dpi=160);plt.close(fig)
 fig,ax=plt.subplots(figsize=(5,4));cm=confusion_matrix(y,probs[selected]['test']>=thresholds[selected]);ax.imshow(cm,cmap='Blues');ax.set(xticks=[0,1],yticks=[0,1],xlabel='Predicted delayed',ylabel='Actual delayed',title=selected+' | test')
 for i in range(2):
  for j in range(2):ax.text(j,i,str(cm[i,j]),ha='center',va='center',color='white' if cm[i,j]>cm.max()/2 else 'black')
 fig.tight_layout();fig.savefig(g/'m4_03_confusion.png',dpi=160);plt.close(fig)
 print(json.dumps(meta,ensure_ascii=False));print(pd.DataFrame(rows).to_string(index=False))
if __name__=='__main__':main()
