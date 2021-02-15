"""
Define command-line options, arguments and sub-commands by using argparse.
"""

import argparse


def arguments():
    parser = argparse.ArgumentParser()

    # Define the subparser
    subparsers = parser.add_subparsers(dest='analyse', required=True)

    # Optimisation
    opt = subparsers.add_parser('opt', help="Compute optimisation of dadi's parameters")
    opt.add_argument(
        '--nb', dest='number', type=int, required=True,
        help="""Determine for a given number of sampled genomes n, the error rate of the
        inference of 100 observed for various mutation rate mu"""
    )

    # Likelihood-ratio test
    lrt = subparsers.add_parser('lrt', help="Comute likelihood-ratio test for dadi inference")
    lrt.add_argument(
        '--param', dest='param', default='tau', choices=['tau', 'kappa', 'tau-kappa'],
        required=True,
        help="Parameter to optimize - (tau) - default, (kappa), (kappa, tau)"
    )
    lrt.add_argument(
        '--value', dest='value', type=float, required=True, nargs='*',
        help="Simulation for a given parameter p"
    )

    # Plot error rate
    er = subparsers.add_parser('er', help="Plot error rate of simulation with dadi")

    return parser.parse_args()
