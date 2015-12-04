from __future__ import division
import numpy as np
import collections
import MCMC_algorithm_pi
import sys

DEFAULT_NSAMP = 1000
DEFAULT_NTHIN = 1
DEFAULT_NBURN = 200

def deduplicate_counts (umi_counts, nsamp=DEFAULT_NSAMP, nthin=DEFAULT_NTHIN, nburn=DEFAULT_NBURN, uniform=True, total_counts = None):

    # Remove zeros from data, to shorten the vector
    data = []
    if uniform:
        for value in umi_counts.values():
            if value > 0:
                data.append(value)
    else:
        total_data = []
        for key, value in umi_counts.items():
            if value > 0:
                data.append(value)
                total_data.append(total_counts[key])

    n = len(data)
    N = sum(data)

    # Set priors for the different parameters
    pi_prior = [1., 1.]
    S_prior = [1.] * n
    if uniform:
        # The 'uniform' algorithm assumes equi-probability for all tags, before amplification
        C_prior = [1./n] * n
    else:
        # The non-uniform algorithm illicits prior from data
        N_total = sum(total_data)
        C_prior = [float(1.5 * total_data[j])/N_total for j in range(n)]
        # Uncomment next line to use only current data to illicit prior
        # C_prior = [float(data[j])/N for j in range(n)]

    # Run Gibbs sampler
    pi_post = MCMC_algorithm_pi.MCMC_algorithm(data, \
                                            n, N, \
                                            S_prior, C_prior, pi_prior, \
                                            nsamp, nthin, nburn, \
                                            True)

    # # Compute median for each tag
    # median_list = [0] * n
    # for i in range(n):
    #     median_list[i] = computeMedian(p_post[i::n])

    # Distribute counts across tags
    p = computeMedian(pi_post)
    data_dedup = apportion_counts(data, round(p * sum(data)))

    # Return ordered dictionary with estimated number of true molecules
    umi_true = collections.OrderedDict()
    for umi, raw_count, dedup in zip(umi_counts.keys(), umi_counts.values(), data_dedup):
        if raw_count == 0:
            umi_true[umi] = raw_count
        else:
            # umi_true[key] = int(np.ceil(median_list[index] * data[index]))
            umi_true[umi] = int(round(dedup))
            assert(umi_true[umi] > 0)

    return umi_true

def computeMedian(list):
    list.sort()
    lens = len(list)
    if lens % 2 != 0:
        midl = int(lens / 2)
        res = list[midl]
    else:
        odd = int((lens / 2) -1)
        ev = int((lens / 2))
        res = float(list[odd] + list[ev]) / float(2)
    return res

def apportion_counts (counts, target_sum):
    divisor = float(target_sum) / sum(counts)
    quotients = (count / divisor for count in counts)
    result = [int(count > 0) for count in counts]
    residuals = [quotient - new_count for quotient, new_count in zip(quotients, result)]
    remaining_counts = target_sum - sum(result)
    while remaining_counts > 0:
        which_to_increment = residuals.index(max(residuals))
        result[which_to_increment] += 1
        residuals[which_to_increment] -= 1
        remaining_counts -= 1
    return result
