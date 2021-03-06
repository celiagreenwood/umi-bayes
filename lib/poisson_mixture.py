import numpy as np
import math
import collections
from numba import jit
from . import apportion_counts, umi_data

DEFAULT_KMAX = 10

# Store results into object
class BICResults:
  def __init__(self, bic, estimate, k):
    self.bic = bic
    self.estimate = estimate
    self.size = k

class PoisMixData:
  def __init__(self, data, obs):
    self.data = data
    self.obs = obs
    self.lgamma_obs = np.array([math.lgamma(x + 1) for x in obs])
    self.size = obs.size

# Functions to compute likelihood
# def dpois(x, mu):
#   return x*np.log(mu) - mu - math.lgamma(x+1)

@jit
def mixture_dist(obs, param, lgamma_obs):
  n = obs.size
  k = param[0].size
  tmp = np.zeros( (n, k) )
  log_mu = np.log(param[1])
  log_pi = np.log(param[0])
  for j in range(n):
    tmp[j,...] = log_pi + obs[j]*log_mu - param[1] - lgamma_obs[j]
  if k == 1:
    return tmp
  else:
    return np.logaddexp.reduce(tmp, 1)

@jit
def likelihood(data, obs, param, lgamma_obs):
  return np.sum(data * mixture_dist(obs, param, lgamma_obs))

# BIC for selecting number of components
@jit
def BIC(data, obs, param, lgamma_obs):
    k = param[0].size
    return -2*likelihood(data, obs, param, lgamma_obs) + (2 * k - 1) * math.log(np.sum(data))

# EM-algorithm updates
@jit
def mixing_weights(obs, param, lgamma_obs):
    k = param[0].size
    n = obs.size
    output = np.zeros((n, k))
    log_mu = np.log(param[1])
    log_pi = np.log(param[0])
    for j in range(n):
      tmp = log_pi+ obs[j]*log_mu - param[1] - lgamma_obs[j]
      output[j,...] = tmp - np.logaddexp.reduce(tmp)
    return output

@jit
def update_param(data, obs, param, lgamma_obs):
  k = param[0].size
  mixing_mat = np.exp(mixing_weights(obs, param, lgamma_obs))
  for j in range(k):
    mixing_mat[...,j] *= data
  tmp = np.sum(mixing_mat, axis = 0)
  next_theta = 1/tmp
  next_prob =tmp/np.sum(data)
  for j in range(k):
    mixing_mat[...,j] *= obs
  next_theta *= np.sum(mixing_mat, axis = 0)
  return (next_prob, next_theta)

# def update_param(data, obs, param, lgamma_obs):
#   k = param[0].size
#   mixing_mat1 = np.exp(mixing_weights(obs, param, lgamma_obs))
#   mixing_mat2 = np.copy(mixing_mat1)
#   obs_data = obs * data
#   for j in range(k):
#     mixing_mat1[...,j] *= data
#     mixing_mat2[...,j] *= obs_data
#   tmp = np.sum(mixing_mat1, axis = 0)
#   next_prob =tmp/np.sum(data)
#   next_theta = np.sum(mixing_mat2, axis = 0)/tmp
#   return (next_prob, next_theta)

# QN1 algorithm----
#We need to check if our proposed updates still lie in the parameter space
@jit
def in_param_space(current_param, param_step):
  k = current_param[0].size
  suggest_prob = current_param[0] + param_step[0:k]
  suggest_theta = current_param[1] + param_step[k:]
  return all(suggest_theta > 0) and all(suggest_prob > 0) and all(suggest_prob < 1)

#Define our updating step for the estimate of the inverse jacobian
@jit
def update_A(current_A, param_step, function_step):
  A_step = np.outer(param_step - np.dot(current_A, function_step), np.dot(param_step, current_A))
  A_step /=  np.dot(param_step, np.dot(current_A, function_step))
  return current_A + A_step

#We are conceptually looking for a zero of g_tilde
@jit
def g_tilde(data, obs, param, lgamma_obs):
  next_param = update_param(data, obs, param, lgamma_obs)
  next_step = (next_param[0] - param[0], next_param[1] - param[1])
  return np.concatenate(next_step)

# Fit algorithm to data----
def QN1_algorithm(data, obs, init_param, lgamma_obs):
    #parameter initialization
    next_param = init_param
    K = init_param[0].size
    next_A =  -np.identity(2*K)
    next_gtilde = g_tilde(data, obs, next_param, lgamma_obs)
    iter = 0
    underflow = False
    if K == 1:
        next_prob = 1.0
        next_theta = float(np.sum(data * obs))/np.sum(data)
        next_param = (np.array(next_prob), np.array(next_theta))
    else:
        next_lkhd = likelihood(data, obs, next_param, lgamma_obs)
        while True:
            iter += 1
            current_param = next_param
            current_gtilde = next_gtilde
            current_A = next_A
            current_lkhd = next_lkhd
            #update parameter
            param_step = -np.dot(current_A, current_gtilde)
            #test if proposed parameter is in parameter space
            while not in_param_space(current_param, param_step):
                param_step /= 2
            #accept proposition
            next_param = (current_param[0] + param_step[0:K],
                                     current_param[1] + param_step[K:])
            #update the other parameters
            next_gtilde = g_tilde(data, obs, next_param, lgamma_obs)
            next_A = update_A(current_A, param_step,
                                             next_gtilde - current_gtilde)
            #testing if stopping rule is met
            next_lkhd = likelihood(data, obs, next_param, lgamma_obs)
            try:
              if math.log(abs(current_lkhd - next_lkhd)) < -6 or iter >= 10000:
                break
            except RuntimeWarning:
              underflow = True
              break
    # Compute BIC
    if iter >= 10000 or underflow:
      bic = float('inf')
    else:
      bic = BIC(data, obs, next_param, lgamma_obs)
    output = BICResults(bic, next_param, K)
    return output

def select_num_comp(data, obs, lgamma_obs, kmax):
  n = data.size
  bic_list = [QN1_algorithm(data, obs, (np.array(k*[1.0/k]), np.arange(1, k+1)), lgamma_obs) for k in range(1, min(kmax, n) + 1)]
  min_bic_result = min(bic_list, key = lambda p: p.bic)
  return min_bic_result

def dedup_cluster(umi_counts, kmax = DEFAULT_KMAX):
  initial_counts = list(umi_counts.nonzero_values())
  if max(initial_counts) == 1: return(umi_counts) # shortcut when there are no duplicates
  naive_est = umi_counts.n_nonzero()
  max_est = sum(initial_counts)
  counter = collections.Counter(initial_counts)
  counter[0] = len(umi_counts) - naive_est
  data = list(counter.values())
  obs = list(counter.keys())
  lgamma_obs = np.array([math.lgamma(x + 1) for x in obs])
  data = np.array(data)
  obs = np.array(obs)
  if data.size <= 2:
    est = naive_est
  else:
    min_bic_result = select_num_comp(data, obs, lgamma_obs, kmax)
    est = 0
    num_mol = np.argsort(min_bic_result.estimate[1])
    mixing_mat = np.exp(mixing_weights(obs, min_bic_result.estimate, lgamma_obs))
    for i in range(data.size):
      if obs[i] == 0:
        continue
      index = np.argmax(mixing_mat[i,...])
      est += num_mol[index] * data[i]
      # est += np.dot(mixing_mat[i,...], num_mol) * obs[i]
    # There is a clear range within which the value must fall
    if est <= naive_est:
      est = naive_est
    elif est >= max_est:
      est = max_est
    else:
      est = int(round(est))
  data_dedup = apportion_counts.apportion_counts(initial_counts, est)
  return umi_data.UmiValues(zip(umi_counts.nonzero_keys(), data_dedup))
