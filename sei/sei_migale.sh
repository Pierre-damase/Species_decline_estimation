#!/bin/bash

# Nom du job
#$ -N dadi_tau

# Number of separate submissions to the cluster
#$ -t 1-10

# Short pour un job < 12h
#$ -q short.q

# Adresse à envoyer
# -M pierre.imbert@college-de-france.fr

# Envoie mail - (b)egin, (e)nd, (a)bort & (s)uspend
# -m as

# Sortie standard
#$ -o $HOME/work/Out

# Sortie d'erreur
#$ -e $HOME/work/Err

conda activate sei-3.8.5
python /home/pimbert/work/Species_evolution_inference/sei/sei_migale.py inf -dadi --model decline --job $SGE_TASK_ID --param tau --value -4
conda deactivate
