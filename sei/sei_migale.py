"""
Migale version.
"""

import os
import random
import time
import warnings
import pandas as pd
import numpy as np
from collections import Counter
from scipy.stats import chi2

from arguments import arguments as arg
from files import files as f
from graphics import plot
from inference import dadi
from simulation import msprime as ms


def computation_theoritical_theta(ne, mu, length):
    """
    Compute the theoritical theta - theta = 4.Ne.mu.L with

      - 4: diploid population
      - Ne: the effective population size
      - mu: the mutation rate
      - L: the size of the simulated genomes
    """
    return (4 * ne * mu * length)


def simulation_parameters(sample, ne, rcb_rate, mu, length):
    """
    Set up the parametres for the simulation
    """
    params = {
        "sample_size": sample, "size_population": ne, "rcb_rate": rcb_rate, "mu": mu,
        "length": length
    }
    return params


def define_parameters(model):
    """
    Define pairs of (Tau, Kappa) - sudden decline/growth model - and (m12, Kappa) - migration
    model.
    """
    if model == 'decline':
        # Range of value for tau & kappa
        params = []
        for tau in np.arange(-4, 2.5, 0.1):
            for kappa in np.arange(-3.5, 3, 0.1):
                params.append({'Tau': round(tau, 2), 'Kappa': round(kappa, 2)})

    else:
        # Range of value for m12 & kappa
        params = []
        for m12 in np.arange(-4, 2.5, 0.1):
            for kappa in np.arange(-3.5, 3, 0.1):
                params.append({'m12': round(m12, 2), 'm21': 0.0, 'Kappa': round(kappa, 2)})

    return params


######################################################################
# Generate a set of SFS with msprime                                 #
######################################################################

def length_from_file(fichier, params, mu, snp):
    """
    Extract length factor from file and return the length of the sequence.
    """
    res = pd.read_json(path_or_buf="{}".format(fichier), typ='frame')

    # Bug of to_json or from_json method of pandas
    # Some value of tau, kappa or m12 with many decimal points
    for i, row in res.iterrows():
        res.at[i, 'Parameters'] = {k: round(v, 2) for k, v in row['Parameters'].items()}

    factor = res[res['Parameters'] == params]['Factor'].values[0]

    return (snp / factor) / (4 * 1 * mu)


def generate_sfs(params, model, nb_simu, path_data, path_length):
    """
    Generate a set of unfolded sfs of fixed SNPs size with msprime.
    """
    # Define length
    length = length_from_file(path_length, params, mu=8e-2, snp=100000)

    # Convert params from log scale
    params.update({k: (np.power(10, v) if k != 'm21' else v) for k, v in params.items()})

    # Parameters for the simulation
    params.update(
        simulation_parameters(sample=20, ne=1, rcb_rate=8e-2, mu=8e-2, length=length))

    sfs, snp, execution = [], [], []
    for _ in range(nb_simu):
        start_time = time.time()

        sfs_observed = ms.msprime_simulation(model=model, params=params)
        sfs.append(sfs_observed)
        snp.append(sum(sfs_observed))

        execution.append(time.time() - start_time)

    # Create DataFrame form dictionary
    dico = {
        'Parameters': [params], 'SFS observed': [sfs], 'SNPs': [snp],
        'Time': [round(np.mean(execution), 4)]
    }
    data = pd.DataFrame(dico)

    # Export DataFrame to json file
    data.to_json("{}".format(path_data))


######################################################################
# Inference with Dadi                                                #
######################################################################

def likelihood_ratio_test(ll_m0, ll_m1, dof):
    """
    Likelihood-ratio test to assesses the godness fit of two model.

    c.f. jupyter notebook "analyse.ipynb" for more information

    Parameters
    ----------
    ll_m0: float
        log-likelihood of model m0
    ll_m1: float
        log-likelihood of model m1
    dof: int
        degree of freedom

    Return
    ------
    Either 1 - test significant and reject of H0
        Or 0 - test insignificant and no reject of H0
    """
    lrt = 2 * (ll_m1 - ll_m0)  # LL ratio test
    p_value = chi2.sf(lrt, dof)  # Chi2 test

    if p_value > 0.05:
        return 0  # test insignificant and no reject of h0
    return 1  # test significant and reject of h0


def weighted_square_distance(sfs):
    """
    Compute the weighted square distance d2.

    c.f. jupyter notebook "analyse.ipynb" for more information

    Parameter
    ---------
    sfs: dictionary
        Either observed SFS and inferred SFS with M1
            Or inferred SFS of two models - M0 & M1

    Return
    ------
    d2: float
        the weighted square distance
    """
    # Normalization of the SFS
    normalized_sfs = {}
    for key, spectrum in sfs.items():
        normalized_sfs[key] = [ele / sum(spectrum) for ele in spectrum]

    # Weighted square distance
    if "Observed" in sfs.keys():
        d2 = [
            np.power(eta_model - eta_obs, 2) / eta_model for eta_obs, eta_model in
            zip(normalized_sfs['Observed'], normalized_sfs['Model'])
        ]
    else:
        d2 = [
            np.power(eta_m0 - eta_m1, 2) / (np.mean([eta_m0, eta_m1])) for eta_m0, eta_m1 in
            zip(normalized_sfs['M0'], normalized_sfs['M1'])
        ]

    del normalized_sfs

    return sum(d2)


def compute_dadi_inference(sfs_observed, models, sample, fold, path_data, job, dof, fixed, value):
    """
    Parameter
    ---------
    sfs_observed: list
        the observed SFS generated with msprime
    models: dictionary
      - Inference
        The model with more parameters, i.e. M1
      - Control
        The model with less parameters, i.e. M0
    sample: int
        The number of sampled monoploid genomes
    fixed: str
        fixed parameter for the inference, either (tau), (kappa) or (migr)
    dof: int
        degrees of freedom

    Return
    ------
    data: dictionary
      - LRT
        Likelihood ratio test
      - M0
        List of log likelihood and sfs for the inference with M0
      - M1
        List of log likelihood, sfs and estimated parameters for the inference with M1.
        In this case from the same observed SFS, 1000 inferences with M1 are made and only the
        best one is kept. I.E. the one with the highest log-likelihood.
      - Time
        Mean execution time for the inference
      - d2 observed inferred
        Weighted square distance between observed SFS & inferred SFS with M1
      - d2 models
        Weighted square distance between inferred SFS with M0 & M1
    """
    data = {
        'LRT': [], 'M0': {'LL': [], 'SFS': []}, 'M1': {'LL': [], 'SFS': [], 'Estimated': []},
        'Time': 0, 'd2 observed inferred': [], 'd2 models': []
    }
    execution = []

    # Grid point for the extrapolation
    pts_list = [sample*10, sample*10 + 10, sample*10 + 20]

    for i, sfs in enumerate(sfs_observed):
        # Generate the SFS file compatible with dadi
        dadi_file = "SFS-{}".format(job) if value is None else "SFS_{}-{}".format(value, job)
        f.dadi_data(sfs, models['Inference'].__name__, fold, path=path_data, name=dadi_file)

        # Dadi inference for M0
        # Pairs (Log-likelihood, Inferred SFS)
        m0_inference = dadi.inference(pts_list, models['Control'], path=path_data,
                                      name=dadi_file)
        data['M0']['LL'].append(m0_inference[0])
        data['M0']['SFS'].append(m0_inference[1])

        # Dadi inference for M1
        m1_inferences, m1_execution = [], []

        for _ in range(100):  # Run 100 inferences with dadi from the observed sfs
            start_inference = time.time()

            # Pairs (Log-likelihood, Inferred SFS, Params)
            tmp = dadi.inference(pts_list, models['Inference'], fixed=fixed, value=value,
                                 path=path_data, name=dadi_file)

            m1_inferences.append(tmp)
            m1_execution.append(time.time() - start_inference)

        execution.append(np.mean(m1_execution))

        # m1_inferences is a list of pairs (Log-likelihood, Inferred SFS, Params)
        # Compare each item of this list by the value at index 0, i.e. the log-likelihood and
        # select the one with this highest value.
        index_best_ll = m1_inferences.index((max(m1_inferences, key=lambda ele: ele[0])))

        data['M1']['LL'].append(m1_inferences[index_best_ll][0])
        data['M1']['SFS'].append(m1_inferences[index_best_ll][1])
        data['M1']['Estimated'].append(m1_inferences[index_best_ll][2])

        # Compute the log-likelihood ratio test between M0 and M1
        data['LRT'].append(
            likelihood_ratio_test(data['M0']['LL'][i], data['M1']['LL'][i], dof)
        )

        # Compute weighted square distance
        data['d2 observed inferred'].append(
            weighted_square_distance({'Observed': sfs, 'Model': data['M1']['SFS'][i]})
        )  # d2 between the observed SFS & inferred SFS with M1

        data['d2 models'].append(
            weighted_square_distance({'M0': data['M0']['SFS'][i], 'M1': data['M1']['SFS'][i]})
        )  # d2 between the inferred SFS of two models - M0 & M1

    # Mean execution time for the inference
    data['Time'] = round(sum(execution) / len(sfs_observed), 4)

    return data


def save_dadi_inference(simulation, models, fold, path_data, job, fixed, value):
    """
    Inference with dadi.

    Parameter
    ---------
    simulation: dictionary
      - Parameters
        Parameters for the simulation with msprime - mutation rate mu, recombination rate, Ne,
        length L of the sequence, sample size.
      - SNPs
        List of SNPs for each observed SFS
      - SFS observed
        List of the observed SFS generated with msprime for the same set of parameters
      - Time
        Mean execution time to generate the observed SFS

    models: dictionary
      - Inference: the custom model to infer demography history, i.e. the model m1 with more
        parameters
      - Control: the control model, i.e. the model m0 with less parameters

    fixed
        fixed parameter for the inference, either (tau), (kappa) or (migration)
    value
        Value of the fixed parameters for the inference - log scale
    """
    # Inference
    sfs_observed, sample = simulation['SFS observed'], simulation['Parameters']['sample_size']

    if value is None:
        inf = compute_dadi_inference(sfs_observed, models, sample, fold, path_data, job, dof=2,
                                     fixed=fixed, value=value)
    else:
        inf = compute_dadi_inference(sfs_observed, models, sample, fold, path_data, job, dof=2,
                                     fixed=fixed, value=np.power(10, value))

    # Save data
    params = {
        k: v for k, v in simulation['Parameters'].items() if k in ['Tau', 'Kappa', 'm12',
                                                                   'm21']
    }
    params['Theta'] = 4 * 1 * 8e-2 * simulation['Parameters']['length']  # 4 * Ne * mu * L

    # Create DataFrame form dictionary
    dico = {
        'Parameters': [params], 'Positive hit': [sum(inf['LRT'])],
        'SNPs': [simulation['SNPs']], 'SFS observed': [sfs_observed], 'M0': [inf['M0']],
        'M1': [inf['M1']], 'Time': [inf['Time']],
        'd2 observed inferred': [np.mean(inf['d2 observed inferred'])],
        'd2 models': [np.mean(inf['d2 models'])]
    }
    data = pd.DataFrame(dico)

    # Export dataframe to json files
    if fixed is None:
        name = "dadi_{}-{}".format(models['Inference'].__name__.split('_')[1], job)
    else:
        name = "dadi_{}_{}={}-{}" \
            .format(models['Inference'].__name__.split('_')[1], fixed, value, job)
    data.to_json("{}{}".format(path_data, name))

    # Remove SFS file
    if value is None:
        os.remove("{}SFS-{}.fs".format(path_data, job))
    else:
        os.remove("{}SFS_{}-{}.fs".format(path_data, np.power(10, value), job))


######################################################################
# Inference with stairway plot 2                                     #
######################################################################


def compute_stairway_inference(simulation, path_stairway, path_data):
    """
    Parameter
    ---------
    path_stairway: path to the folder stairway_plot__es
    path_data: path to the blueprint file
    """
    # Data
    stairway = pd.DataFrame(columns=['M0', 'M1', 'Ne', 'Year'])

    tau_list, kappa_list = [-4., 0., 2.4, -3.2], [-3.5, 0., 2.9]
    if 'Tau' in simulation['Parameters']:
        tau = round(np.log10(simulation['Parameters']['Tau']), 2)
        kappa = round(np.log10(simulation['Parameters']['Kappa']), 2)
    elif 'm12' in simulation['Parameters']:
        tau = round(np.log10(simulation['Parameters']['m12']), 2)
        kappa = round(np.log10(simulation['Parameters']['Kappa']), 2)
    else:
        tau = None

    # Inference - the inference is only done with one of the observed SFS among the 100 for a
    # matter of efficiency
    all_sfs = simulation['SFS observed']
    sfs = np.array([sum(spectrum) for spectrum in zip(*all_sfs)]) / len(all_sfs)

    blueprint = "stairway_inference"

    # Generate the SFS file compatible with stairway plot v2
    data = {
        k: v for k, v in simulation['Parameters'].items() if k in ['sample_size', 'mu']
    }
    data['sfs'], data['year'], data['ninput'] = sfs, 1, 200

    f.stairway_data(blueprint, data, path_data)

    # Create the batch file
    os.system("java -cp {0}stairway_plot_es Stairbuilder {1}{2}.blueprint"
              .format(path_stairway, path_data, blueprint))

    # Run the batch file
    os.system("bash {}{}.blueprint.sh".format(path_data, blueprint))

    # Extract data from the inference with stairway
    dico = f.read_stairway_final("{}{}/final/".format(path_data, blueprint))
    dico.update(f.read_stairway_summary("{0}{1}/{1}.final.summary".format(path_data,
                                                                          blueprint)))

    # keep track of parameters
    if 'Kappa' in simulation['Parameters'].keys():
        dico['Parameters'] = {
            k: v for k, v in simulation['Parameters'].items() if k in ['Tau', 'Kappa', 'm12']
        }
    else:
        dico['Parameters'] = simulation['Parameters']['Ne']

    # Summarize the data of the inference
    stairway = stairway.append(dico, ignore_index=True)

    # Keep track of some figure generated by stairway plot
    if tau in tau_list and kappa in kappa_list:
        figure = "{0}{1}/{1}.final.summary.png".format(path_data, blueprint)
        tmp = "/home/pimbert/work/Species_evolution_inference/Figures/Stairway/"
        os.system("mv {} {}stairway-tau={}_kappa={}".format(figure, tmp, tau, kappa))

    # Remove all blueprint file & stairway file
    os.system("rm -rf {}".format(path_data))

    return stairway


def save_stairway_inference(simulation, model):
    """
    Inference with stairway plot 2.

    Parameter
    ---------
    simulation: dictionary
      - Parameters
        Parameters for the simulation with msprime - mutation rate mu, recombination rate, Ne,
        length L of the sequence, sample size.
      - SNPs
        List of SNPs for each observed SFS
      - SFS observed
        List of the observed SFS generated with msprime for the same set of parameters
      - Time
        Mean execution time to generate the observed SFS

    model: str
        either decline, migration or cst
    """
    # Set up path data
    path_stairway = "/home/pimbert/work/Species_evolution_inference/sei/" \
        "inference/stairway_plot_v2.1.1/"

    if model == 'decline':
        param = {k: round(np.log10(v), 2) for k, v in simulation['Parameters'].items()
                 if k in ['Tau', 'Kappa']}
        file_data = "stairway_{}-tau={}_kappa={}".format(model, param['Tau'], param['Kappa'])
        path_data = path_stairway + file_data + "/"

    elif model == 'migration':
        param = {k: round(np.log10(v), 2) for k, v in simulation['parameters'].items()
                 if k in ['m12', 'kappa']}
        file_data = "stairway_{}-m12={}_kappa={}".format(model, param['m12'], param['kappa'])
        path_data = path_stairway + file_data + "/"

    else:
        param = None
        file_data = "stairway_{}-ne={}/".format(model, simulation['Parameters']['Ne'])
        path_data = path_stairway + file_data + "/"

    if not os.path.isdir(path_data):
        os.mkdir(path_data)
        os.system("cp -r {} {}".format(path_stairway + "stairway_plot_es", path_data))

    # Compute the inference with stairway plot 2
    data = compute_stairway_inference(simulation, path_stairway, path_data)

    # Convert pandas DataFrame data to json file
    data.to_json("{}{}-all".format(path_stairway, file_data))


######################################################################
# Main                                                               #
######################################################################

if __name__ == "__main__":
    # inference(msprime_model=ms.sudden_decline_model, dadi_model=dadi.sudden_decline_model,
    #           control_model=dadi.constant_model, optimization="tau")

    args = arg.arguments()

    if args.analyse == 'data':

        # Simulation of sudden decline model with msprime for various tau & kappa
        if args.model == 'decline':
            params = define_parameters(args.model)
            params, model = params[args.job-1], ms.sudden_decline_model

            path_data = "/home/pimbert/work/Species_evolution_inference/Data/Msprime/{0}/" \
                "SFS_{0}-tau={1}_kappa={2}" \
                .format(args.model, params['Tau'], params['Kappa'])

        # Simulation of two populations migration models for various migration into 1 from
        # 2 (with m12 the migration rate) and no migration into 2 from 1
        # Population 1 size is pop1 and population 2 size is pop2 = kappa*pop1
        elif args.model == 'migration':
            # params = define_parameters(args.model)

            # SUPR #
            tmp = define_parameters(args.model)
            data = pd.read_json("/home/pimbert/work/Species_evolution_inference/Data/Msprime/migration/SFS_migration-all")

            # Mean SNPs
            data['SNPs'] = data['SNPs'].apply(np.mean)

            params, present = [], []
            for _, row in data.iterrows():
                p = {'m12': round(np.log10(row['Parameters']['m12']), 2), 'm21': 0.0,
                     'Kappa': round(np.log10(row['Parameters']['Kappa']), 2)}
                present.append(p)
                if not 80000 < row['SNPs'] < 120000:
                    params.append(p)

            for p in tmp:
                if p not in present:
                    params.append(p)
            # SUPR #

            params, model = params[args.job-1], ms.twopops_migration_model

            path_data = "/home/pimbert/work/Species_evolution_inference/Data/Msprime/{0}/" \
                "SFS_{0}-m12={1}_kappa={2}" \
                .format(args.model, params['m12'], params['Kappa'])

        path_length = "/home/pimbert/work/Species_evolution_inference/Data/Msprime/" \
            "length_factor-{}".format(args.model)

        generate_sfs(params, model, nb_simu=100, path_data=path_data, path_length=path_length)

    elif args.analyse == 'inf':
        path_data = "/home/pimbert/work/Species_evolution_inference/Data/Msprime/{}/" \
            .format(args.model)
        filin = "SFS_{}-all".format(args.model)

        # Export the observed SFS to DataFrame
        simulation = f.export_simulation_files(filin, path_data)

        # Inference with dadi
        if args.dadi:

            # Set up M0 & M1 model for the inference with dadi
            if args.model == "decline":
                models = \
                    {'Inference': dadi.sudden_decline_model, 'Control': dadi.constant_model}
            elif args.model == "migration":
                models = \
                    {'Inference': dadi.twopops_migration_model, 'Control': dadi.constant_model}

            # Select observed data for the inference
            if args.param is None:
                simulation = simulation.iloc[args.job-1]
                path_data = "/home/pimbert/work/Species_evolution_inference/Data/Dadi/{}/all/" \
                    .format(args.model)

            else:
                if args.param != 'm12':
                    param = args.param.capitalize()
                else:
                    param = args.param

                simulation = [
                    ele for _, ele in simulation.iterrows()
                    if round(np.log10(ele['Parameters'][param]), 2) == args.value
                ][args.job-1]

                path_data = "/home/pimbert/work/Species_evolution_inference/Data/Dadi/{}/{}/" \
                    .format(args.model, args.param)

            path_data += "Folded" if args.fold else "Unfolded/"

            save_dadi_inference(simulation, models, args.fold, path_data, args.job,
                                fixed=args.param, value=args.value)

        # Inference with stairway plot v2
        elif args.stairway:
            simulation = simulation.iloc[args.job - 1]
            save_stairway_inference(simulation, model=args.model)

