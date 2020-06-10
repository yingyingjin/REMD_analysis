import os
import sys
import glob
import pickle
import pymbar
import natsort
import argparse
import time as time
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
from matplotlib import rc
from matplotlib import cm
from tqdm.auto import tqdm
from alchemlyb.parsing.gmx import extract_dHdl, extract_u_nk
from alchemlyb.preprocessing import equilibrium_detection
from alchemlyb.estimators import BAR, MBAR, TI


def initialize(args):
    parser = argparse.ArgumentParser(
        description='This code analyzes the dhdl files generated by replica \
                    exchange molecular dynamics (REMD) simulations to perform \
                    free energy calculations, plot overlap matrices and estimate\
                    the Wang-Landau weights for expanded ensemble.')
    parser.add_argument('-d',
                        '--dir',
                        type=str,
                        default='.',
                        help='The directory where the dhdl files are.')
    parser.add_argument('-t',
                        '--temp',
                        type=float,
                        default=298.15,
                        help='The temperature in Kelvin the simulation was performed at.')
    parser.add_argument('-dt',
                        '--dt',
                        type=float,
                        default=0.2,
                        help='The time step used in the dhdl files')
                        
    args_parse = parser.parse_args(args)

    return args_parse

def ordinal(n):
    ordinal_str = "%d%s" % (n,"tsnrhtdd"[(n/10%10!=1)*(n%10<4)*n%10::4])
    return ordinal_str


def preprocess_data(dir, temp, dt):
    # extract and subsample dHdl using equilibrium_detection
    dHdl_state, dHdl = [], []  # dHdl_state is for collecting data for a single state
    u_nk_state, u_nk = [], []  # u_nk_state is for collecting data fro a single state

    if os.path.isfile('temporary.xvg') is True:
        os.system("rm temporary.xvg")
    files = glob.glob(os.path.join(dir, '*.xvg*'))
    files = natsort.natsorted(files, reverse=False)

    file_idx = -1  
    n = 0     # counter for the number of files of a specific state
    n_state = 0   # counter for the number of states
    
    for i in tqdm(files):
        n += 1
        file_idx += 1 
        print("Parsing %s and collecting data ..." % files[file_idx])
        os.system("head -n-1 %s > temporary.xvg" % i)  # delete the last line in case it is incomplete
        
        dHdl_state.append(extract_dHdl('temporary.xvg', T=temp))
        u_nk_state.append(extract_u_nk('temporary.xvg', T=temp))

        if n > 1:  # for discard the overlapped time frames of the previous file
            upper_t = dHdl_state[-2].iloc[dHdl_state[-2].shape[0] - 1].name[0]   # the last time frame of file n
            lower_t = dHdl_state[-1].iloc[0].name[0]   # the first time frame of file n + 1 
            # upper_t and lower_t should be the same for both dHdl and u_nk

            if lower_t != 0:   # in case that the file n+1 is the first file of the next replica
                n_discard = int((upper_t - lower_t) / dt + 1)   # number of data frames to discard in file n
                dHdl_state[-2] = dHdl_state[-2].iloc[:-n_discard]
                u_nk_state[-2] = u_nk_state[-2].iloc[:-n_discard]
            else:  # lower_t == 0 means that we have gathered dHdl for the previous state
                n_state += 1
                dHdl_data = pd.concat(dHdl_state[:-1])
                u_nk_data = pd.concat(u_nk_state[:-1])

                dHdl.append(equilibrium_detection(dHdl_data, dHdl_data.iloc[:, 0]))
                dHdl_state = [dHdl_state[-1]]
                print('Subsampling dHdl data of the %s state ...' % ordinal(n_state))

                u_nk.append(equilibrium_detection(u_nk_data, u_nk_data.iloc[:, 0]))
                u_nk_state = [u_nk_state[-1]]
                print('Subsampling u_nk data of the %s state ...' % ordinal(n_state))

                n = 1   # now there is only one file loaded in dHdl_state/u_nk_state

    # dealing with the last state with equilibrium_detaction
    n_state += 1
    dHdl_data = pd.concat(dHdl_state)
    u_nk_data = pd.concat(u_nk_state)

    dHdl.append(equilibrium_detection(dHdl_data, dHdl_data.iloc[:, 0]))
    print('Subsampling dHdl data of the %s state ...' % ordinal(n_state))

    u_nk.append(equilibrium_detection(u_nk_data, u_nk_data.iloc[:, 0]))
    print('Subsampling u_nk data of the %s state ...\n' % ordinal(n_state))

    dHdl = pd.concat(dHdl)
    u_nk = pd.concat(u_nk)
    print("Data preprocessing completed!\n")
    
    os.system("rm temporary.xvg")

    return dHdl, u_nk


def free_energy_calculation(dHdl, u_nk):
    print("Fitting TI on dHdl ...")
    ti = TI().fit(dHdl)

    print("Fitting BAR on u_nk ...")
    bar = BAR().fit(u_nk)

    print("Fitting MBAR on u_nk ...")
    mbar = MBAR().fit(u_nk)

    print("====== Results ======")
    print("TI: {} +/- {} kT".format(ti.delta_f_.iloc[0, -1], ti.d_delta_f_.iloc[0, -1]))
    print("BAR: {} +/- {} kT".format(bar.delta_f_.iloc[0, -1], "unknown"))
    print("MBAR: {} +/- {} kT".format(mbar.delta_f_.iloc[0, -1], mbar.d_delta_f_.iloc[0, -1]))

    return ti, bar#, mbar

def get_overlap_matrix(u_nk):
    # sort by state so that rows from same state are in contiguous blocks
    u_nk = u_nk.sort_index(level=u_nk.index.names[1:])

    groups = u_nk.groupby(level=u_nk.index.names[1:])
    N_k = [(len(groups.get_group(i)) if i in groups.groups else 0) for i in u_nk.columns]        

    MBAR = pymbar.mbar.MBAR(u_nk.T, N_k)
    overlap_matrix = np.array(MBAR.computeOverlap()['matrix'])

    return overlap_matrix

def plot_matrix(matrix, png_name, start_idx=0):
    sns.set_context(rc={
    'family': 'sans-serif',
    'sans-serif': ['DejaVu Sans'],
    'size': 5
    })

    K = len(matrix)
    plt.figure(figsize=(K / 3, K / 3))
    annot_matrix = np.zeros([K, K])   # matrix for annotating values

    mask = []
    for i in range(K):
        mask.append([])
        for j in range(len(matrix[0])):
            if matrix[i][j] < 0.005:            
                mask[-1].append(True)
            else:
                mask[-1].append(False)

    for i in range(K):
        for j in range(K):
            annot_matrix[i, j] = round(matrix[i, j], 2)

    x_tick_labels = y_tick_labels = np.arange(start_idx, start_idx + K)
    ax = sns.heatmap(matrix, cmap="YlGnBu", linecolor='silver', linewidth=0.25,
                    annot=annot_matrix, square=True, mask=mask, fmt='.2f', cbar=False, xticklabels=x_tick_labels, yticklabels=y_tick_labels)
    ax.xaxis.tick_top()
    ax.tick_params(length=0)
    cmap = cm.get_cmap('YlGnBu')   # to get the facecolor
    ax.set_facecolor(cmap(0))      # use the brightest color (value = 0)
    for _, spine in ax.spines.items():
        spine.set_visible(True)    # add frames to the heat map
    plt.annotate('$\lambda$', xy=(0, 0), xytext=(-0.45, -0.20))
    plt.title('Overlap matrix', fontsize=14, weight='bold')
    plt.tight_layout(pad=1.0)

    plt.savefig(png_name, dpi=600)
    # plt.show()
    plt.close()


def main():

    rc('font', **{
        'family': 'sans-serif',
        'sans-serif': ['DejaVu Sans'],
        'size': 10
    })
    # Set the font used for MathJax - more on this later
    rc('mathtext', **{'default': 'regular'})
    plt.rc('font', family='serif')

    t1 = time.time()
    args = initialize(sys.argv[1:])

    sys.stdout = open("Result.txt", "w")
    if os.path.isfile('alchemical_analysis.pickle') is True:
        print('Loading the preprocessed data dHdl and u_nk ...')
        with open('alchemical_analysis.pickle', 'rb') as handle:
            data = pickle.load(handle)
        dHdl = data[0]
        u_nk = data[1]
    else:
        print('Preprocessing the data in the dhdl files ...')
        dHdl, u_nk = preprocess_data(args.dir, args.temp, dt=args.dt)
        with open('alchemical_analysis.pickle', 'wb') as handle:
            pickle.dump([dHdl, u_nk], handle, protocol=pickle.HIGHEST_PROTOCOL)

    print("\nPerforming free energy calculations ...")
    ti, bar = free_energy_calculation(dHdl, u_nk)

    print("\nCalculating Wang-Landau weights for expanded ensemble using TI ...")
    WL_weights = ""
    """
    print(ti.delta_f_)
    print(ti.delta_f_.iloc[0])
    print(ti.delta_f_.iloc[0][0])
    print(ti.delta_f_.iloc[0][1])
    print(ti.delta_f_.iloc[0][2])
    """
    for i in range(len(ti.delta_f_.iloc[0])):
        WL_weights += (' ' + str(round(ti.delta_f_.iloc[0][i], 5)))
    print('Estimated Wang-Landau weights: %s' % WL_weights)
    print("\nComputing and visualizing the overlap matrix ...")
    # sys_name = 'PLCpep7'
    #matrix = get_overlap_matrix(u_nk)
    #plot_matrix(matrix, 'overlap_matrix.png')
    t2 = time.time()
    print("Time elapsed: %s seconds." % (t2 - t1))

    sys.stdout.close()

